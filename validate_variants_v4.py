import csv
import html
import os
import re
import sys
from collections import Counter, defaultdict

# Usage:
#   python validate_variants_v4.py export_converted.csv
#
# Logic:
# 1. Empty trailing variation columns are fine when they are consistently empty for the whole group.
#    Example: all SKUs only use Variation_Value_1 -> OK.
# 2. If SKUs in the same group use a different number of active variation attributes -> ERROR.
#    Example: one SKU has only value 1, another has value 1 + value 2 -> ERROR.
# 3. If one value in a variation attribute looks like the wrong type compared with the rest
#    of that same attribute in the group -> ERROR.
#    Example: Variation_Value_1 is mostly colors/materials, but one SKU has "10" -> ERROR.

input_file = sys.argv[1] if len(sys.argv) > 1 else "export_converted.csv"

output_dir = "output"
os.makedirs(output_dir, exist_ok=True)

GROUP_COL = "Variation_Group-ID"
SKU_COL = "ID"
LEVEL_COL = "salsify:data_inheritance_hierarchy_level_id"

VARIANT_COLS = [
    "Variation_Value_1 - en",
    "Variation_Value_2 - en",
    "Variation_Value_3 - en",
]

def is_empty(value):
    return value is None or str(value).strip() == ""

def clean(value):
    return str(value or "").strip()

def active_attribute_count(row):
    """Counts non-empty variation values from left to right."""
    return sum(1 for col in VARIANT_COLS if not is_empty(row.get(col)))

def value_type(value):
    """
    Basic type detection for logical validation.
    This intentionally stays dependency-free for GitHub Actions.
    """
    v = clean(value)

    if is_empty(v):
        return "empty"

    # Pure number: 10, 1.0, 2, 12.5
    if re.fullmatch(r"\d+(\.\d+)?", v):
        return "number"

    # Dimension-like value: 20x50, 20 x 50, 20x50x10
    if re.fullmatch(r"\d+(\.\d+)?\s*x\s*\d+(\.\d+)?(\s*x\s*\d+(\.\d+)?)*", v.lower()):
        return "dimension"

    # Number with unit: 20 cm, 1.5 kg
    if re.fullmatch(r"\d+(\.\d+)?\s*[a-zA-Z]+", v):
        return "number_with_unit"

    return "text"

def majority_type(values):
    types = [value_type(v) for v in values if not is_empty(v)]
    if not types:
        return "empty"
    return Counter(types).most_common(1)[0][0]

def safe_get(row, col):
    return clean(row.get(col, ""))

groups = defaultdict(list)

with open(input_file, newline="", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)

    required_columns = [GROUP_COL, SKU_COL, LEVEL_COL] + VARIANT_COLS
    missing_columns = [c for c in required_columns if c not in reader.fieldnames]

    if missing_columns:
        raise ValueError(f"Missing required columns in CSV: {missing_columns}")

    for row in reader:
        group_id = safe_get(row, GROUP_COL)
        if group_id:
            groups[group_id].append(row)

issues = []
summary = []

for group_id, rows in groups.items():
    child_rows = [
        r for r in rows
        if safe_get(r, LEVEL_COL).lower() == "child"
    ]

    group_issue_count = 0

    # Rule 1: each child SKU in the group should use the same number of variation attributes.
    count_by_sku = []
    for row in child_rows:
        sku = safe_get(row, SKU_COL)
        count_by_sku.append((sku, active_attribute_count(row), row))

    active_counts = [item[1] for item in count_by_sku]

    if active_counts:
        expected_count = Counter(active_counts).most_common(1)[0][0]
    else:
        expected_count = 0

    for sku, actual_count, row in count_by_sku:
        if actual_count != expected_count:
            issues.append({
                "Group_ID": group_id,
                "SKU": sku,
                "Attribute": "Variation attributes",
                "Issue": "Inconsistent number of variation attributes in group",
                "Expected": str(expected_count),
                "Actual": str(actual_count),
                "Value": " | ".join(safe_get(row, col) for col in VARIANT_COLS),
            })
            group_issue_count += 1

    # Rule 2: for each active variation column, the value type should be logical within the group.
    # Only check columns that are used by the group.
    for col in VARIANT_COLS:
        values = [safe_get(r, col) for r in child_rows if not is_empty(r.get(col))]

        # Avoid false positives with tiny samples.
        if len(values) < 3:
            continue

        expected_type = majority_type(values)

        for row in child_rows:
            sku = safe_get(row, SKU_COL)
            value = safe_get(row, col)

            if is_empty(value):
                continue

            actual_type = value_type(value)

            # Flag only clear mismatch against group pattern.
            if actual_type != expected_type:
                issues.append({
                    "Group_ID": group_id,
                    "SKU": sku,
                    "Attribute": col,
                    "Issue": "Non-logical value type compared with other SKUs in same group",
                    "Expected": expected_type,
                    "Actual": actual_type,
                    "Value": value,
                })
                group_issue_count += 1

    summary.append({
        "Group_ID": group_id,
        "Total_Rows": len(rows),
        "Child_SKUs": len(child_rows),
        "Expected_Active_Attributes": expected_count,
        "Issues": group_issue_count,
    })

def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

issue_fields = [
    "Group_ID",
    "SKU",
    "Attribute",
    "Issue",
    "Expected",
    "Actual",
    "Value",
]

summary_fields = [
    "Group_ID",
    "Total_Rows",
    "Child_SKUs",
    "Expected_Active_Attributes",
    "Issues",
]

issues_csv = os.path.join(output_dir, "validation_issues.csv")
summary_csv = os.path.join(output_dir, "validation_summary.csv")

write_csv(issues_csv, issue_fields, issues)
write_csv(summary_csv, summary_fields, summary)

def csv_to_html(csv_file, html_file, title):
    with open(csv_file, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))

    total_records = max(len(rows) - 1, 0)

    html_rows = []
    for index, row in enumerate(rows):
        tag = "th" if index == 0 else "td"
        cells = "".join(f"<{tag}>{html.escape(str(cell))}</{tag}>" for cell in row)
        html_rows.append(f"<tr>{cells}</tr>")

    page = f"""<!doctype html>
<html>
<head>
    <meta charset="utf-8">
    <title>{html.escape(title)}</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 24px;
            background: #f7f8fa;
            color: #222;
        }}
        .card {{
            background: white;
            border: 1px solid #ddd;
            border-radius: 10px;
            padding: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }}
        h1 {{
            margin-top: 0;
        }}
        .count {{
            margin-bottom: 16px;
            color: #555;
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
            background: white;
        }}
        th, td {{
            border: 1px solid #ddd;
            padding: 8px;
            text-align: left;
            vertical-align: top;
        }}
        th {{
            background: #f0f2f5;
            position: sticky;
            top: 0;
        }}
        tr:nth-child(even) {{
            background: #fafafa;
        }}
        tr:hover {{
            background: #fff4cc;
        }}
    </style>
</head>
<body>
    <div class="card">
        <h1>{html.escape(title)}</h1>
        <div class="count">Rows in report: {total_records}</div>
        <table>
            {''.join(html_rows)}
        </table>
    </div>
</body>
</html>"""

    with open(html_file, "w", encoding="utf-8") as f:
        f.write(page)

csv_to_html(
    issues_csv,
    os.path.join(output_dir, "validation_issues.html"),
    "Validation Issues"
)

csv_to_html(
    summary_csv,
    os.path.join(output_dir, "validation_summary.html"),
    "Validation Summary"
)

print("Validation complete.")
print(f"Groups checked: {len(groups)}")
print(f"Issues found: {len(issues)}")
print(f"Open: {os.path.join(output_dir, 'validation_issues.html')}")

# Fail the GitHub Action when issues are found.
if issues:
    sys.exit(1)
