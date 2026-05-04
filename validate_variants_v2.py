
import csv
import os
import sys
from collections import defaultdict

input_file = sys.argv[1] if len(sys.argv) > 1 else "export_converted.csv"

output_dir = "output"
os.makedirs(output_dir, exist_ok=True)

group_col = "Variation_Group-ID"
level_col = "salsify:data_inheritance_hierarchy_level_id"

variant_cols = [
    "Variation_Value_1 - en",
    "Variation_Value_2 - en",
    "Variation_Value_3 - en",
]

def is_empty(val):
    return val is None or str(val).strip() == ""

groups = defaultdict(list)

with open(input_file, newline='', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        groups[row[group_col]].append(row)

issues = []
summary = []

for group_id, rows in groups.items():
    child_rows = [r for r in rows if r[level_col].lower() == "child"]

    seen_combinations = set()

    for r in child_rows:
        values = tuple(r.get(col, "").strip() for col in variant_cols)

        if any(is_empty(v) for v in values):
            issues.append([group_id, "missing_variant_value", values])

        if values in seen_combinations:
            issues.append([group_id, "duplicate_variant_combination", values])
        else:
            seen_combinations.add(values)

    summary.append([group_id, len(rows), len(child_rows), len(seen_combinations)])

# Write CSV outputs
with open(os.path.join(output_dir, "validation_issues.csv"), "w", newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow(["group_id", "issue", "values"])
    writer.writerows(issues)

with open(os.path.join(output_dir, "validation_summary.csv"), "w", newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow(["group_id", "total_rows", "child_rows", "unique_variant_combinations"])
    writer.writerows(summary)

# HTML generator
def csv_to_html(csv_file, html_file, title):
    with open(csv_file, newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        rows = list(reader)

    html = f"""
    <html>
    <head>
        <title>{title}</title>
        <style>
            body {{ font-family: Arial; margin: 20px; }}
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; }}
            th {{ background: #f4f4f4; }}
            tr:nth-child(even) {{ background: #f9f9f9; }}
        </style>
    </head>
    <body>
        <h1>{title}</h1>
        <table>
    """

    for i, row in enumerate(rows):
        html += "<tr>"
        for cell in row:
            tag = "th" if i == 0 else "td"
            html += f"<{tag}>{cell}</{tag}>"
        html += "</tr>"

    html += "</table></body></html>"

    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html)

csv_to_html(
    os.path.join(output_dir, "validation_issues.csv"),
    os.path.join(output_dir, "validation_issues.html"),
    "Validation Issues"
)

csv_to_html(
    os.path.join(output_dir, "validation_summary.csv"),
    os.path.join(output_dir, "validation_summary.html"),
    "Validation Summary"
)

print("Validation complete. Check output/ folder.")
