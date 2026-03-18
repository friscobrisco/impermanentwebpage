#!/usr/bin/env python3
"""
Generate the final index.html by injecting leaderboard data into the template.

Reads:  data/leaderboard.json
        templates/dashboard.html
Writes: index.html
"""

import json
import os
import sys


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)

    data_path = os.path.join(repo_root, "data", "leaderboard.json")
    template_path = os.path.join(repo_root, "templates", "dashboard.html")
    output_path = os.path.join(repo_root, "index.html")

    # Read data
    if not os.path.exists(data_path):
        print(f"ERROR: Data file not found: {data_path}", file=sys.stderr)
        sys.exit(1)

    with open(data_path, "r") as f:
        data = json.load(f)

    # Read template
    if not os.path.exists(template_path):
        print(f"ERROR: Template not found: {template_path}", file=sys.stderr)
        sys.exit(1)

    with open(template_path, "r") as f:
        template = f.read()

    # Inject data — replace placeholder with the DATA object
    # Use compact JSON (no extra whitespace) to keep file size small
    data_json = json.dumps(data, separators=(",", ":"))
    data_line = f"const DATA = {data_json};"

    if "/* __DATA_PLACEHOLDER__ */" not in template:
        print("ERROR: Placeholder '/* __DATA_PLACEHOLDER__ */' not found in template", file=sys.stderr)
        sys.exit(1)

    html = template.replace("/* __DATA_PLACEHOLDER__ */", data_line)

    # Write output
    with open(output_path, "w") as f:
        f.write(html)

    file_size = os.path.getsize(output_path)
    print(f"Generated {output_path} ({file_size:,} bytes)")


if __name__ == "__main__":
    main()
