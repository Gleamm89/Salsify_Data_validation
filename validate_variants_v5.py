import csv
import html
import os
import re
import sys
from collections import Counter, defaultdict

# Usage:
#   python validate_variants_v5.py export_converted.csv
#
# Output:
#   output/validation_issues.csv
#   output/validation_issues.html
#   output/validation_summary.csv
#   output/validation_summary.html
#
# Main report behavior:
#   When a group has an issue, the HTML report displays the WHOLE group.
#   Variation values are shown under their matching Variation_Name fields.
#
# Example:
#   If Variation_Name_1 = Colour and Variation_Value_1 = Black,
#   the HTML table will show a column named Colour with value Black.

input_file = sys.argv[1] if len(sys.argv) > 1 else "export_converted.csv"

output_dir = "output"
os.makedirs(output_dir, exist_ok=True)

GROUP_COL = "Variation_Group-ID"
SKU_COL = "ID"
LEVEL_COL = "salsify:data_inheritance_hierarchy_level_id"

VARIANT_VALUE_COLS = [
    "Variation_Value_1 - en",
    "Variation_Value_2 - en",
    "Variation_Value_3 - en",
]

# Different exports sometimes contain Name_1, sometimes only Name_2/Name_3.
# The script will use these if present; otherwise it falls back to Attribute_1/2/3.
VARIANT_NAME_COLS = [
    "Variation_Name_1 - en",
    "Variation_Name_2 - en",
    "Variation_Name_3 - en",
]

def is_empty(value):
    return value is None or str(value).strip() == ""

def clean(value):
    return str(value or "").strip()

def safe_get(row, col):
    return clean(row.get(col, ""))

def active_attribute_count(row):
    return sum(1 for col in VARIANT_VALUE_COLS if not is_empty(row.get(col)))

def value_type(value):
    v = clean(value)

    if is_empty(v):
        return "empty"

    if re.fullmatch(r"\d+(\.\d+)?", v):
        return "number"

    if re.fullmatch(r"\d+(\.\d+)?\s*x\s*\d+(\.\d+)?(\s*x\s*\d+(\.\d+)?)*", v.lower()):
        return "dimension"

    if re.fullmatch(r"\d+(\.\d+)?\s*[a-zA-Z]+", v):
        return "number_with_unit"

    return "text"

def majority_type(values):
    types = [value_type(v) for v in values if not is_empty(v)]
    if not types:
        return "empty"
    return Counter(types).most_common(1)[0][0]

def get_group_attribute_names(rows):
    """
    Determines display names for the variation columns.
    It looks at Variation_Name_1/2/3 values in the group.
    If a name is missing, it falls back to Attribute_1/2/3.
    """
    names = []

    for index, name_col in enumerate(VARIANT_NAME_COLS):
        found_names = []
        for row in rows:
            if name_col in row and not is_empty(row.get(name_col)):
                found_names.append(safe_get(row, name_col))

        if found_names:
            names.append(Counter(found_names).most_common(1)[0][0])
        else:
            names.append(f"Attribute_{index + 1}")

    return names

groups = defaultdict(list)

with open(input_file, newline="", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames or []

    required_columns = [GROUP_COL, SKU_COL, LEVEL_COL] + VARIANT_VALUE_COLS
    missing_columns = [c for c in required_columns if c not in fieldnames]

    if missing_columns:
        raise ValueError(f"Missing required columns in CSV: {missing_columns}")

    for row in reader:
        group_id = safe_get(row, GROUP_COL)
        if group_id:
            groups[group_id].append(row)

issues = []
summary = []
issue_group_ids = set()

for group_id, rows in groups.items():
    child_rows = [
        r for r in rows
        if safe_get(r, LEVEL_COL).lower() == "child"
    ]

    group_issue_count = 0

    # Rule 1: all child SKUs in a group should use the same number of active variation attributes.
    count_by_sku = []
    for row in child_rows:
        sku = safe_get(row, SKU_COL)
        count_by_sku.append((sku, active_attribute_count(row), row))

    active_counts = [item[1] for item in count_by_sku]
    expected_count = Counter(active_counts).most_common(1)[0][0] if active_counts else 0

    for sku, actual_count, row in count_by_sku:
        if actual_count != expected_count:
            issue_group_ids.add(group_id)
            issues.append({
                "Group_ID": group_id,
                "SKU": sku,
                "Attribute": "Variation attributes",
                "Issue": "Inconsistent number of variation attributes in group",
                "Expected": str(expected_count),
                "Actual": str(actual_count),
                "Value": " | ".join(safe_get(row, col) for col in VARIANT_VALUE_COLS),
            })
            group_issue_count += 1

    # Rule 2: each variation attribute should have a logical value type within the group.
    for col in VARIANT_VALUE_COLS:
        values = [safe_get(r, col) for r in child_rows if not is_empty(r.get(col))]

        if len(values) < 3:
            continue

        expected_type = majority_type(values)

        for row in child_rows:
            sku = safe_get(row, SKU_COL)
            value = safe_get(row, col)

            if is_empty(value):
                continue

            actual_type = value_type(value)

            if actual_type != expected_type:
                issue_group_ids.add(group_id)
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

def build_issue_lookup():
    lookup = defaultdict(list)
    for issue in issues:
        lookup[(issue["Group_ID"], issue["SKU"])].append(issue["Issue"])
    return lookup

def build_group_details_html(html_file):
    issue_lookup = build_issue_lookup()

    group_sections = []

    for group_id in sorted(issue_group_ids):
        rows = groups[group_id]
        child_rows = [
            r for r in rows
            if safe_get(r, LEVEL_COL).lower() == "child"
        ]

        attr_names = get_group_attribute_names(rows)

        group_issues = [i for i in issues if i["Group_ID"] == group_id]

        issue_rows = []
        for issue in group_issues:
            issue_rows.append(
                "<tr>"
                f"<td>{html.escape(issue['SKU'])}</td>"
                f"<td>{html.escape(issue['Attribute'])}</td>"
                f"<td>{html.escape(issue['Issue'])}</td>"
                f"<td>{html.escape(issue['Expected'])}</td>"
                f"<td>{html.escape(issue['Actual'])}</td>"
                f"<td>{html.escape(issue['Value'])}</td>"
                "</tr>"
            )

        value_headers = "".join(f"<th>{html.escape(name)}</th>" for name in attr_names)

        item_rows = []
        for row in child_rows:
            sku = safe_get(row, SKU_COL)
            active_count = active_attribute_count(row)
            row_issues = issue_lookup.get((group_id, sku), [])

            values = [
                safe_get(row, value_col)
                for value_col in VARIANT_VALUE_COLS
            ]

            value_cells = "".join(
                f"<td>{html.escape(value)}</td>"
                for value in values
            )

            issue_text = "; ".join(row_issues)
            css_class = "issue-row" if row_issues else ""

            item_rows.append(
                f"<tr class='{css_class}'>"
                f"<td>{html.escape(group_id)}</td>"
                f"<td>{html.escape(sku)}</td>"
                f"<td>{html.escape(str(active_count))}</td>"
                f"{value_cells}"
                f"<td>{html.escape(issue_text)}</td>"
                "</tr>"
            )

        section = f"""
        <section class="group-card">
            <h2>Group {html.escape(group_id)}</h2>
            <h3>Issues</h3>
            <table>
                <tr>
                    <th>SKU</th>
                    <th>Attribute</th>
                    <th>Issue</th>
                    <th>Expected</th>
                    <th>Actual</th>
                    <th>Value</th>
                </tr>
                {''.join(issue_rows)}
            </table>

            <h3>Whole group values</h3>
            <table>
                <tr>
                    <th>Group_ID</th>
                    <th>SKU</th>
                    <th>Active attribute count</th>
                    {value_headers}
                    <th>Issue on SKU</th>
                </tr>
                {''.join(item_rows)}
            </table>
        </section>
        """
        group_sections.append(section)

    page = f"""<!doctype html>
<html>
<head>
    <meta charset="utf-8">
    <title>Validation Group Details</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 24px;
            background: #f7f8fa;
            color: #222;
        }}
        h1 {{
            margin-top: 0;
        }}
        .summary {{
            background: white;
            border: 1px solid #ddd;
            border-radius: 10px;
            padding: 16px 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }}
        .group-card {{
            background: white;
            border: 1px solid #ddd;
            border-radius: 10px;
            padding: 18px 20px;
            margin-bottom: 24px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
            margin-bottom: 18px;
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
        .issue-row {{
            background: #ffe6e6 !important;
            font-weight: 600;
        }}
        .empty {{
            color: #999;
        }}
    </style>
</head>
<body>
    <div class="summary">
        <h1>Validation Group Details</h1>
        <p>Groups checked: {len(groups)}</p>
        <p>Groups with issues: {len(issue_group_ids)}</p>
        <p>Total issues: {len(issues)}</p>
    </div>
    {''.join(group_sections)}
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

build_group_details_html(
    os.path.join(output_dir, "validation_group_details.html")
)

print("Validation complete.")
print(f"Groups checked: {len(groups)}")
print(f"Groups with issues: {len(issue_group_ids)}")
print(f"Issues found: {len(issues)}")
print(f"Open: {os.path.join(output_dir, 'validation_group_details.html')}")

if issues:
    sys.exit(1)
