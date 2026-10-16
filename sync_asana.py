#!/usr/bin/env python3
"""
Mars Creative Tracker — Asana sync
Reads tasks from the CRM project (Design & Copy sections in effem.com workspace)
and writes data.json consumed by the dashboard.

Run locally:
    ASANA_PAT=your_token python sync_asana.py

Run via GitHub Actions:
    Set ASANA_PAT as a repository secret.
"""

import os
import json
import re
import sys
from datetime import date, timedelta

import requests

# ─── Config ──────────────────────────────────────────────────────────────
PAT = os.environ.get("ASANA_PAT")
if not PAT:
    sys.exit("Error: ASANA_PAT environment variable is not set.")

HEADERS = {"Authorization": f"Bearer {PAT}", "Accept": "application/json"}
BASE    = "https://app.asana.com/api/1.0"

PROJECT_CRM = "1211470217061171"   # effem.com › CRM project

# Section GID → dashboard stage
SECTION_STAGES = {
    "1216017794527920": "with-copy",  # Copy
    "1216017794527921": "design",     # Design
    "1213434583781873": "waiting",    # On Hold
}

# Design section GID (used to fetch completed tasks)
DESIGN_SECTION = "1216017794527921"

# Assignees who are part of the creative design team
DESIGN_TEAM_EMAILS = {"lucia.mora@within.co"}

TODAY = date.today().isoformat()
THIRTY_DAYS_AGO = (date.today() - timedelta(days=30)).isoformat()

# ─── Helpers ─────────────────────────────────────────────────────────────
BRAND_PATTERNS = [
    (r'\bCSR\b|Caesar|CESAR',      "CSR"),
    (r'\bGRN\b|Greenies|GREENIES', "GRN"),
    (r'\bIAM\b|IAMS',              "IAM"),
    (r'\bPED\b|Pedigree|PEDIGREE', "PED"),
    (r'\bNTR\b|Nutro|NUTRO',       "NTR"),
    (r'\bSHB\b|Sheba|SHEBA',       "SHB"),
    (r'\bTEM\b|Temptations',       "TEM"),
]

def detect_brand(name: str) -> str | None:
    for pattern, code in BRAND_PATTERNS:
        if re.search(pattern, name):
            return code
    return None

def detect_type(name: str) -> str:
    n = name.lower()
    if "enrichment" in n:
        return "Enrichment"
    if "birthday" in n:
        return "Birthday"
    if "holiday" in n or "contingency" in n:
        return "Holiday"
    return "General"

def short_id(gid: str) -> int:
    return int(gid) % 100000

def initials(name: str) -> str:
    parts = name.split()
    return (parts[0][0] + parts[-1][0]).upper() if len(parts) >= 2 else name[:2].upper()

def clean_name(raw: str) -> str:
    # Strip CRM milestone suffix: "…_CRM_Final Email Design" → "…"
    name = re.sub(r'_CRM_.*$', '', raw)
    # Strip trailing date pattern e.g. "_10/15/2026"
    name = re.sub(r'_\d{1,2}/\d{1,2}/\d{4}$', '', name)
    return name.strip()

def asana_get(path: str, params: dict = None) -> dict:
    resp = requests.get(f"{BASE}{path}", headers=HEADERS, params=params or {}, timeout=20)
    resp.raise_for_status()
    return resp.json()

# ─── Fetch tasks ─────────────────────────────────────────────────────────
def fetch_incomplete_tasks(section_gid: str) -> list:
    data = asana_get(f"/sections/{section_gid}/tasks", {
        "opt_fields": "gid,name,due_on,assignee.name,assignee.gid,num_comments",
        "limit": 100,
    })
    return data.get("data", [])

def fetch_completed_tasks(section_gid: str) -> list:
    data = asana_get(f"/sections/{section_gid}/tasks", {
        "opt_fields": "gid,name,due_on,assignee.name,completed",
        "limit": 50,
        "completed_since": THIRTY_DAYS_AGO,
    })
    return [t for t in data.get("data", []) if t.get("completed")]

# ─── Build tasks list ─────────────────────────────────────────────────────
tasks = []
seen_gids = set()

# Incomplete tasks from each tracked section
for section_gid, stage in SECTION_STAGES.items():
    for t in fetch_incomplete_tasks(section_gid):
        gid = t["gid"]
        if gid in seen_gids:
            continue
        brand = detect_brand(t["name"])
        if not brand:
            continue
        seen_gids.add(gid)

        assignee = t.get("assignee") or {}
        due      = t.get("due_on") or ""

        # Escalate design tasks that are overdue → Final Steps
        eff_stage = stage
        if stage == "design" and due and due < TODAY:
            eff_stage = "final"

        # Determine delivery sub-status placeholders
        tasks.append({
            "id":        short_id(gid),
            "asana_gid": gid,
            "name":      clean_name(t["name"]),
            "brand":     brand,
            "stage":     eff_stage,
            "type":      detect_type(t["name"]),
            "who":       initials(assignee.get("name", "")) if assignee else "—",
            "due":       due,
            "cmt":       t.get("num_comments", 0),
        })

# Completed tasks from Design section → Delivered
for t in fetch_completed_tasks(DESIGN_SECTION):
    gid = t["gid"]
    if gid in seen_gids:
        continue
    brand = detect_brand(t["name"])
    if not brand:
        continue
    seen_gids.add(gid)

    assignee = t.get("assignee") or {}
    tasks.append({
        "id":        short_id(gid),
        "asana_gid": gid,
        "name":      clean_name(t["name"]),
        "brand":     brand,
        "stage":     "delivered",
        "type":      detect_type(t["name"]),
        "who":       initials(assignee.get("name", "")) if assignee else "—",
        "due":       t.get("due_on") or "",
        "cmt":       0,
        "int":       1,
        "ext":       0,
    })

# ─── Write output ─────────────────────────────────────────────────────────
output = {
    "updated_at": TODAY,
    "source":     "asana-live",
    "project":    PROJECT_CRM,
    "tasks":      tasks,
}

out_path = "data.json"
with open(out_path, "w") as f:
    json.dump(output, f, indent=2)

print(f"✓ Wrote {len(tasks)} tasks → {out_path}  (updated {TODAY})")
