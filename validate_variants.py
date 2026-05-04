#!/usr/bin/env python3
"""
Validate variant completeness and logical consistency in a vidaXL/Salsify CSV export.

Designed to run without external Python packages.

Usage:
    python validate_variants.py data/export_converted.csv

Outputs:
    output/validation_issues.csv
    output/validation_summary.csv

Exit codes:
    0 = no blocking issues found
    1 = blocking issues found
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Tuple


# ---- Configure your file columns here -------------------------------------------------
ID_COL = "ID"
GROUP_COL = "Variation_Group-ID"
LEVEL_COL = "salsify:data_inheritance_hierarchy_level_id"
PARENT_ID_COL = "salsify:parent_id"

VARIATION_VALUE_COLS = [
    "Variation_Value_1 - en",
    "Variation_Value_2 - en",
    "Variation_Value_3 - en",
]

VARIATION_NAME_COLS = [
    "Variation_Name_2 - en",
    "Variation_Name_3 - en",
]

# Attributes that should normally be the same for every child in a variation group.
# Add more columns here if needed, for example: "Name - en", "CMS_Amazon_Title - en".
MUST_BE_SAME_WITHIN_GROUP: List[str] = []

# Values treated as missing.
MISSING_TOKENS = {"", "null", "none", "n/a", "na", "nan", "-"}


# ---- Helpers -------------------------------------------------------------------------
def clean(value: object) -> str:
    """Normalize cell values for comparison."""
    if value is None:
        return ""
    text = str(value).strip()
    # Collapse repeated whitespace to a single space.
    text = re.sub(r"\s+", " ", text)
    return text


def is_missing(value: object) -> bool:
    return clean(value).lower() in MISSING_TOKENS


def norm(value: object) -> str:
    """Case-insensitive normalized value used for duplicate/consistency checks."""
    return clean(value).casefold()


def row_id(row: Dict[str, str], row_number: int) -> str:
    return clean(row.get(ID_COL)) or f"row_{row_number}"


def issue(
    issues: List[Dict[str, str]],
    severity: str,
    issue_type: str,
    row_number: int | str,
    product_id: str,
    group_id: str,
    column: str,
    message: str,
    value: str = "",
) -> None:
    issues.append(
        {
            "severity": severity,
            "issue_type": issue_type,
            "row_number": str(row_number),
            "id": product_id,
            "variation_group_id": group_id,
            "column": column,
            "value": value,
            "message": message,
        }
    )


def read_csv(path: str) -> Tuple[List[str], List[Tuple[int, Dict[str, str]]]]:
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV has no header row.")
        rows = [(i, row) for i, row in enumerate(reader, start=2)]  # Excel-style row numbers
        return list(reader.fieldnames), rows


def validate_required_columns(headers: Iterable[str]) -> List[str]:
    required = [ID_COL, GROUP_COL, LEVEL_COL, PARENT_ID_COL] + VARIATION_VALUE_COLS
    return [col for col in required if col not in headers]


# ---- Validation logic ----------------------------------------------------------------
def validate(headers: List[str], rows: List[Tuple[int, Dict[str, str]]]) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    issues: List[Dict[str, str]] = []
    groups: Dict[str, List[Tuple[int, Dict[str, str]]]] = defaultdict(list)
    summary_counter = Counter()

    for row_number, row in rows:
        group_id = clean(row.get(GROUP_COL))
        product_id = row_id(row, row_number)
        level = clean(row.get(LEVEL_COL))

        if is_missing(group_id):
            issue(
                issues,
                "error",
                "missing_group_id",
                row_number,
                product_id,
                group_id,
                GROUP_COL,
                "Variation group ID is missing.",
            )
        else:
            groups[group_id].append((row_number, row))

        if is_missing(level):
            issue(
                issues,
                "warning",
                "missing_hierarchy_level",
                row_number,
                product_id,
                group_id,
                LEVEL_COL,
                "Hierarchy level is missing; expected Parent or Child.",
            )

    for group_id, group_rows in groups.items():
        parent_rows = [(rn, r) for rn, r in group_rows if norm(r.get(LEVEL_COL)) == "parent"]
        child_rows = [(rn, r) for rn, r in group_rows if norm(r.get(LEVEL_COL)) == "child"]

        summary_counter["groups"] += 1
        summary_counter["parent_rows"] += len(parent_rows)
        summary_counter["child_rows"] += len(child_rows)

        # A variation group normally needs exactly one parent and at least one child.
        if len(parent_rows) == 0:
            issue(
                issues,
                "error",
                "missing_parent_in_group",
                "group",
                "",
                group_id,
                LEVEL_COL,
                "Variation group has no Parent row.",
            )
        elif len(parent_rows) > 1:
            issue(
                issues,
                "error",
                "multiple_parents_in_group",
                "group",
                ", ".join(row_id(r, rn) for rn, r in parent_rows),
                group_id,
                LEVEL_COL,
                "Variation group has multiple Parent rows.",
            )

        if len(child_rows) == 0:
            issue(
                issues,
                "error",
                "missing_child_in_group",
                "group",
                "",
                group_id,
                LEVEL_COL,
                "Variation group has no Child rows.",
            )

        # Determine which variation columns are active in this group.
        # A column is active if at least one child has a non-empty value in it.
        active_value_cols = [
            col
            for col in VARIATION_VALUE_COLS
            if any(not is_missing(r.get(col)) for _, r in child_rows)
        ]

        # Child rows should not have gaps in active variation values.
        # Example: if Value_1 and Value_2 are active for this group, each child should have both.
        for row_number, row in child_rows:
            product_id = row_id(row, row_number)
            missing_active_cols = [col for col in active_value_cols if is_missing(row.get(col))]
            if missing_active_cols:
                issue(
                    issues,
                    "error",
                    "missing_active_variation_value",
                    row_number,
                    product_id,
                    group_id,
                    "; ".join(missing_active_cols),
                    "Child row is missing one or more variation values that are used by other children in the same group.",
                )

            # Flag gaps like Value_1 empty but Value_2 filled.
            seen_filled_after_gap = False
            gap_cols = []
            found_gap = False
            for col in VARIATION_VALUE_COLS:
                if is_missing(row.get(col)):
                    if any(not is_missing(row.get(next_col)) for next_col in VARIATION_VALUE_COLS[VARIATION_VALUE_COLS.index(col) + 1 :]):
                        found_gap = True
                        gap_cols.append(col)
                elif found_gap:
                    seen_filled_after_gap = True
            if seen_filled_after_gap:
                issue(
                    issues,
                    "warning",
                    "variation_value_gap",
                    row_number,
                    product_id,
                    group_id,
                    "; ".join(gap_cols),
                    "Variation values have a gap; earlier value column is empty while a later value column is filled.",
                )

        # Parent rows usually should not contain child-level variation values.
        for row_number, row in parent_rows:
            product_id = row_id(row, row_number)
            filled_parent_values = [col for col in VARIATION_VALUE_COLS if not is_missing(row.get(col))]
            if filled_parent_values:
                issue(
                    issues,
                    "warning",
                    "parent_has_variation_values",
                    row_number,
                    product_id,
                    group_id,
                    "; ".join(filled_parent_values),
                    "Parent row contains variation values. Usually these should only be on Child rows.",
                )

        # Duplicate child combinations inside the same group are usually not logical.
        combo_map: Dict[Tuple[str, ...], List[Tuple[int, Dict[str, str]]]] = defaultdict(list)
        for row_number, row in child_rows:
            combo = tuple(norm(row.get(col)) for col in active_value_cols)
            if combo:
                combo_map[combo].append((row_number, row))

        for combo, duplicate_rows in combo_map.items():
            if len(duplicate_rows) > 1:
                issue(
                    issues,
                    "error",
                    "duplicate_variation_combination",
                    "group",
                    ", ".join(row_id(r, rn) for rn, r in duplicate_rows),
                    group_id,
                    "; ".join(active_value_cols),
                    "Multiple child rows have the same variation value combination within the same group.",
                    value=" | ".join(combo),
                )

        # Variation names should be consistent within a group when filled.
        for col in VARIATION_NAME_COLS:
            values = sorted({norm(r.get(col)) for _, r in group_rows if not is_missing(r.get(col))})
            if len(values) > 1:
                issue(
                    issues,
                    "warning",
                    "inconsistent_variation_name",
                    "group",
                    "",
                    group_id,
                    col,
                    "Variation name differs within the same group.",
                    value=" | ".join(values),
                )

        # Optional business rules: columns that must be identical across all children.
        for col in MUST_BE_SAME_WITHIN_GROUP:
            if col not in headers:
                continue
            values = sorted({norm(r.get(col)) for _, r in child_rows if not is_missing(r.get(col))})
            if len(values) > 1:
                issue(
                    issues,
                    "warning",
                    "inconsistent_group_attribute",
                    "group",
                    "",
                    group_id,
                    col,
                    "Attribute has different values across child rows in the same group.",
                    value=" | ".join(values[:20]),
                )

    summary = [
        {"metric": "total_rows", "value": str(len(rows))},
        {"metric": "total_groups", "value": str(summary_counter["groups"])},
        {"metric": "parent_rows", "value": str(summary_counter["parent_rows"])},
        {"metric": "child_rows", "value": str(summary_counter["child_rows"])},
        {"metric": "total_issues", "value": str(len(issues))},
        {"metric": "error_issues", "value": str(sum(1 for i in issues if i["severity"] == "error"))},
        {"metric": "warning_issues", "value": str(sum(1 for i in issues if i["severity"] == "warning"))},
    ]

    issue_counts = Counter(i["issue_type"] for i in issues)
    for issue_type, count in issue_counts.most_common():
        summary.append({"metric": f"issue_type::{issue_type}", "value": str(count)})

    return issues, summary


def write_csv(path: str, rows: List[Dict[str, str]], fieldnames: List[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate variant completeness and consistency in a CSV file.")
    parser.add_argument("input_csv", help="Path to input CSV file")
    parser.add_argument("--output-dir", default="output", help="Directory for validation reports")
    args = parser.parse_args()

    headers, rows = read_csv(args.input_csv)
    missing_cols = validate_required_columns(headers)
    if missing_cols:
        print("Missing required columns:", ", ".join(missing_cols), file=sys.stderr)
        return 1

    issues, summary = validate(headers, rows)

    issues_path = os.path.join(args.output_dir, "validation_issues.csv")
    summary_path = os.path.join(args.output_dir, "validation_summary.csv")

    write_csv(
        issues_path,
        issues,
        ["severity", "issue_type", "row_number", "id", "variation_group_id", "column", "value", "message"],
    )
    write_csv(summary_path, summary, ["metric", "value"])

    error_count = sum(1 for i in issues if i["severity"] == "error")
    warning_count = sum(1 for i in issues if i["severity"] == "warning")

    print(f"Rows checked: {len(rows)}")
    print(f"Groups checked: {sum(1 for s in summary if s['metric'] == 'total_groups' for _ in [s]) and summary[1]['value']}")
    print(f"Errors: {error_count}")
    print(f"Warnings: {warning_count}")
    print(f"Issue report: {issues_path}")
    print(f"Summary report: {summary_path}")

    return 1 if error_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
