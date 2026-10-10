"""Recover OOXML geometry only after the complete blueprint text matches the source."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from chemo_agent_product.core.config import Settings
from chemo_agent_product.core.domain import fingerprint
from chemo_agent_product.persistence.database import open_pool

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
W = "{" + NS["w"] + "}"


def attribute(node, path, key="val", default=None):
    found = node.find(path, NS)
    return found.get(W + key, default) if found is not None else default


def norm(value):
    return re.sub(r"\s+", "", str(value or ""))


def paragraph_text(node):
    return "".join(
        child.text or "" if child.tag == W + "t" else "\t" if child.tag == W + "tab" else "\n"
        for child in node.iter()
        if child.tag in {W + "t", W + "tab", W + "br", W + "cr"}
    )


def paragraph(node, styles):
    style = attribute(node, "w:pPr/w:pStyle", default="Normal")
    inherited = styles.get(style, {})
    runs = []
    for run in node.findall(".//w:r", NS):
        font = (
            attribute(run, "w:rPr/w:rFonts", "eastAsia")
            or attribute(run, "w:rPr/w:rFonts", "ascii")
            or inherited.get("font")
        )
        size = attribute(run, "w:rPr/w:sz") or inherited.get("size")
        bold = run.find("w:rPr/w:b", NS)
        italic = run.find("w:rPr/w:i", NS)
        runs.append(
            {
                "text": paragraph_text(run),
                "font": font,
                "size_pt": float(size) / 2 if size else None,
                "bold": bold is not None and bold.get(W + "val", "true") not in {"0", "false"},
                "italic": italic is not None
                and italic.get(W + "val", "true") not in {"0", "false"},
                "underline": attribute(run, "w:rPr/w:u"),
                "color": attribute(run, "w:rPr/w:color"),
            }
        )
    before = attribute(node, "w:pPr/w:spacing", "before", 0)
    after = attribute(node, "w:pPr/w:spacing", "after", 0)
    return {
        "kind": "paragraph",
        "text": paragraph_text(node),
        "style": style,
        "runs": runs,
        "alignment": attribute(node, "w:pPr/w:jc", default=inherited.get("alignment", "left")),
        "space_before_pt": float(before or 0) / 20,
        "space_after_pt": float(after or 0) / 20,
        "page_break_before": node.find("w:pPr/w:pageBreakBefore", NS) is not None,
    }


def table(node, styles):
    grid = [int(c.get(W + "w", "0")) for c in node.findall("w:tblGrid/w:gridCol", NS)]
    cells = []
    matrix = []
    active = {}
    rows = node.findall("w:tr", NS)
    for r, tr in enumerate(rows):
        column = int(attribute(tr, "w:trPr/w:gridBefore", default=0))
        row = [""] * column
        next_active = {}
        for tc in tr.findall("w:tc", NS):
            span = int(attribute(tc, "w:tcPr/w:gridSpan", default=1))
            merge = tc.find("w:tcPr/w:vMerge", NS)
            continuation = merge is not None and merge.get(W + "val") != "restart"
            paragraphs = [paragraph(p, styles) for p in tc.findall("w:p", NS)]
            value = "\n".join(p["text"] for p in paragraphs)
            if continuation:
                anchor = active.get(column)
                if anchor is None or anchor["column_span"] != span:
                    raise ValueError("unresolved vertical merge")
                anchor["row_span"] += 1
                value = anchor["text"]
                next_active[column] = anchor
            else:
                cell = {
                    "row": r,
                    "column": column,
                    "column_span": span,
                    "row_span": 1,
                    "text": value,
                    "paragraphs": paragraphs,
                    "vertical_align": attribute(tc, "w:tcPr/w:vAlign", default="center"),
                }
                cells.append(cell)
                if merge is not None:
                    next_active[column] = cell
            row.extend([norm(value)] * span)
            column += span
        # OOXML rows may omit unused trailing grid positions; preserve physical cells,
        # but compare those empty positions with python-docx's rectangular blueprint.
        row.extend([""] * max(0, len(grid) - column))
        active = next_active
        matrix.append(row)
    return {
        "kind": "table",
        "rows": len(rows),
        "columns": len(grid),
        "grid_twips": grid,
        "cells": cells,
        "matrix": matrix,
    }


def read_word(path: Path):
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
        styles = {}
        if "word/styles.xml" in archive.namelist():
            for style in ET.fromstring(archive.read("word/styles.xml")).findall("w:style", NS):
                styles[style.get(W + "styleId")] = {
                    "font": attribute(style, "w:rPr/w:rFonts", "eastAsia")
                    or attribute(style, "w:rPr/w:rFonts", "ascii"),
                    "size": attribute(style, "w:rPr/w:sz"),
                    "alignment": attribute(style, "w:pPr/w:jc"),
                }
        body = root.find("w:body", NS)
        blocks = []
        for index, node in enumerate(body):
            if node.tag == W + "p":
                block = paragraph(node, styles)
            elif node.tag == W + "tbl":
                block = table(node, styles)
            else:
                continue
            block["source_body_index"] = index
            blocks.append(block)
        section = body.find("w:sectPr", NS)
        page = {
            "width_twips": int(attribute(section, "w:pgSz", "w", 11906)),
            "height_twips": int(attribute(section, "w:pgSz", "h", 16838)),
            "margin_left_twips": int(attribute(section, "w:pgMar", "left", 1440)),
            "margin_right_twips": int(attribute(section, "w:pgMar", "right", 1440)),
        }
    return blocks, page


def signature(block):
    if block["kind"] == "paragraph":
        return ["p", norm(block["text"])]
    if "matrix" in block:
        return ["t", block["matrix"]]
    if isinstance(block.get("rows"), list):
        return ["t", [[norm(v) for v in r] for r in block["rows"]]]
    rows = [[""] * block["columns"] for _ in range(block["rows"])]
    for cell in block["cells"]:
        for col in range(
            cell["column"], min(block["columns"], cell["column"] + cell.get("column_span", 1))
        ):
            rows[cell["row"]][col] = norm(cell["text"])
    return ["t", rows]


def verified_layout(blueprint: dict, sources: list[tuple[Path, list, dict]]):
    target = [signature(b) for b in blueprint["blocks"]]
    matches = []
    for path, blocks, page in sources:
        by_index = {b["source_body_index"]: b for b in blocks}
        explicit = [b.get("source_body_index") for b in blueprint["blocks"]]
        if all(type(i) is int and i in by_index for i in explicit):
            located = [by_index[i] for i in explicit]
            if [signature(b) for b in located] == target:
                matches.append((path, located, page))
        candidates = [i for i, b in enumerate(blocks) if signature(b) == target[0]]
        for start in candidates:
            if [signature(b) for b in blocks[start : start + len(target)]] == target:
                matches.append((path, blocks[start : start + len(target)], page))
    unique = {
        fingerprint(
            {
                "blocks": [
                    {k: v for k, v in b.items() if k != "source_body_index"} for b in blocks
                ],
                "page": page,
            }
        ): (path, blocks, page)
        for path, blocks, page in matches
    }
    if len(unique) != 1:
        return None, {
            "state": "NO_EXACT_SOURCE_MATCH" if not unique else "AMBIGUOUS_LAYOUT",
            "matches": len(matches),
        }
    path, blocks, page = next(iter(unique.values()))
    clean = [
        {**{k: v for k, v in block.items() if k != "matrix"}, "blueprint_block_index": i}
        for i, block in enumerate(blocks)
    ]
    return {"schema_version": "word-layout.v1", "page": page, "blocks": clean}, {
        "state": "VERIFIED_TEXT_AND_GEOMETRY",
        "source_filename": path.name,
        "source_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
        "first_body_index": blocks[0]["source_body_index"],
        "block_count": len(blocks),
        "table_count": sum(b["kind"] == "table" for b in blocks),
        "matches": len(matches),
    }


async def recover(dsn: str, paths: list[Path], apply: bool):
    sources = [(p, *read_word(p)) for p in paths]
    pool = await open_pool(dsn)
    report = []
    try:
        async with pool.acquire() as c, c.transaction():
            rows = await c.fetch(
                "SELECT regimen_version_id,document_tree FROM regimen_catalog.form_blueprint"
            )
            for row in rows:
                payload, result = verified_layout(row["document_tree"], sources)
                report.append({"version_id": str(row["regimen_version_id"]), **result})
                if payload and apply:
                    await c.execute(
                        """INSERT INTO catalog_bridge.form_layout_asset
                      (created_by_principal,regimen_version_id,source_artifact_hash,blueprint_hash,schema_version,content_hash,layout_payload,verification_report)
                      VALUES('product-layout-recovery',$1,$2,$3,'word-layout.v1',$4,$5,$6) ON CONFLICT DO NOTHING""",
                        row["regimen_version_id"],
                        result["source_hash"],
                        fingerprint(row["document_tree"]),
                        fingerprint(payload),
                        payload,
                        result,
                    )
    finally:
        await pool.close()
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--source", action="append", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings(_env_file=args.env_file)
    report = asyncio.run(recover(settings.database_url.get_secret_value(), args.source, args.apply))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    counts = {}
    for item in report:
        counts[item["state"]] = counts.get(item["state"], 0) + 1
    print({"apply": args.apply, "counts": counts, "report": str(args.report)})


if __name__ == "__main__":
    main()
