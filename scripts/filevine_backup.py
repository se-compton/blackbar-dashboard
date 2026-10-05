#!/usr/bin/env python3
"""
Filevine continuity backup: pulls org configuration and case data straight from
the Filevine API v2 gateway using a Personal Access Token (PAT). Works with no
Claude connector involved. Standard library only.

Output lands in ./filevine_backup/ (git-ignored). It can contain client PII,
so keep it on firm-controlled storage. Never commit it; this repo is public.

Setup (Filevine org admin):
  1. In Filevine, create a Personal Access Token plus API client ID/secret
     (Filevine Help Center: "Authenticate Requests to the API Gateway").
  2. export FV_PAT=...  FV_CLIENT_ID=...  FV_CLIENT_SECRET=...
     Optional: FV_ORG_ID (defaults to the first org on the token),
               FV_IDENTITY_URL, FV_API_BASE, FV_SCOPE (see defaults below;
               confirm against the current Filevine developer docs).

Usage:
  python scripts/filevine_backup.py config            # types, phases, sections, fields, taskflows, users
  python scripts/filevine_backup.py projects          # every project, de-identified (no client names)
  python scripts/filevine_backup.py projects --full   # every project, all fields (contains PII)
  python scripts/filevine_backup.py marketing --since 2024-10-01   # marketing source + outcome per PI case
  python scripts/filevine_backup.py case 15148094     # one case: contacts, notes/tasks/emails, deadlines, every tab, docs list
  python scripts/filevine_backup.py cases             # EVERY case as above, one JSON file each (contains PII)
  python scripts/filevine_backup.py cases --since 2025-01-01 --workers 6   # only cases created since a date
  python scripts/filevine_backup.py all               # config + projects (de-identified)

The cases run is resumable: stop it any time and run the same command again;
cases already saved in filevine_backup/cases/ are skipped. Failures are logged
to filevine_backup/cases/_errors.log and retried on the next run. Expect roughly
45 API calls per case, so all ~69,000 cases takes a day or more; raise --workers
only if Filevine isn't returning rate-limit errors. Document files themselves
are not downloaded, only the list of documents on each case.
"""
import argparse
import csv
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.error
import urllib.parse
import urllib.request

IDENTITY_URL = os.environ.get("FV_IDENTITY_URL", "https://identity.filevine.com/connect/token")
API_BASE = os.environ.get("FV_API_BASE", "https://api.filevineapp.com/fv-app/v2").rstrip("/")
SCOPE = os.environ.get(
    "FV_SCOPE",
    "fv.api.gateway.access tenant filevine.v2.api.* openid email fv.auth.tenant.read",
)
OUT = os.environ.get("FV_OUT", "filevine_backup")
PAGE = 1000


class Client:
    def __init__(self):
        for var in ("FV_PAT", "FV_CLIENT_ID", "FV_CLIENT_SECRET"):
            if not os.environ.get(var):
                sys.exit(f"Missing env var {var}. See the header of this file.")
        self.token = None
        self.token_exp = 0
        self.org_id = os.environ.get("FV_ORG_ID")
        self.user_id = None
        self._lock = threading.Lock()
        self._login()

    def _login(self):
        body = urllib.parse.urlencode({
            "grant_type": "personal_access_token",
            "token": os.environ["FV_PAT"],
            "client_id": os.environ["FV_CLIENT_ID"],
            "client_secret": os.environ["FV_CLIENT_SECRET"],
            "scope": SCOPE,
        }).encode()
        req = urllib.request.Request(IDENTITY_URL, data=body, headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        with urllib.request.urlopen(req) as r:
            tok = json.load(r)
        self.token = tok["access_token"]
        # Tokens last ~20 minutes; refresh a little early.
        self.token_exp = time.time() + int(tok.get("expires_in", 1200)) - 120
        who = self._request("POST", "/utils/GetUserOrgsWithToken", auth_only=True)
        self.user_id = str(who["user"]["userId"]["native"])
        if not self.org_id:
            self.org_id = str(who["orgs"][0]["orgId"])

    def _request(self, method, path, params=None, auth_only=False):
        if time.time() > self.token_exp and not auth_only:
            with self._lock:
                if time.time() > self.token_exp:
                    self._login()
        url = API_BASE + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if not auth_only:
            headers["x-fv-orgid"] = self.org_id
            headers["x-fv-userid"] = self.user_id
        data = b"" if method == "POST" else None
        for attempt in range(5):
            try:
                req = urllib.request.Request(url, data=data, method=method, headers=headers)
                with urllib.request.urlopen(req) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 4:
                    time.sleep(2 ** attempt * 2)
                    continue
                raise

    def get(self, path, **params):
        return self._request("GET", path, params)

    def paged(self, path, **params):
        offset = 0
        while True:
            page = self.get(path, offset=offset, limit=PAGE, **params)
            items = page.get("items", []) if isinstance(page, dict) else page
            yield from items
            if not (isinstance(page, dict) and page.get("hasMore")):
                return
            offset += len(items) or PAGE


def save(name, data):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"  wrote {path}")


def native(v):
    return v.get("native") if isinstance(v, dict) else v


def export_config(c):
    print("Config ...")
    for name, fetch in [
        ("me.json", lambda: c.get("/users/me")),
        ("users.json", lambda: list(c.paged("/users"))),
        ("chaintypes.json", lambda: list(c.paged("/chaintypes"))),
    ]:
        try:
            save(name, fetch())
        except urllib.error.HTTPError as e:
            print(f"  skipped {name}: HTTP {e.code}")
    types = list(c.paged("/projecttypes"))
    for t in types:
        tid = native(t["projectTypeId"])
        t["phases"] = list(c.paged(f"/projecttypes/{tid}/phases"))
        sections = list(c.paged(f"/projecttypes/{tid}/sections"))
        for s in sections:
            try:
                s["definition"] = c.get(f"/projecttypes/{tid}/sections/{s['sectionSelector']}")
            except urllib.error.HTTPError as e:
                s["definition_error"] = e.code
        t["sections"] = sections
        try:
            t["taskflows"] = list(c.paged(f"/projecttypes/{tid}/taskflows"))
        except urllib.error.HTTPError as e:
            t["taskflows_error"] = e.code
    save("projecttypes.json", types)


# Only these terms survive from a project name, so a client name can never leak
# into the de-identified exports (hyphenated surnames split into odd segments).
CASE_TERMS = {
    "mva": "MVA", "minor": "Minor", "wc": "WC", "premise": "Premise", "premises": "Premise",
    "dog bite": "Dog Bite", "med mal": "Med Mal", "medmal": "Med Mal", "deceased": "Deceased",
    "product liability": "Product Liability", "nursing home": "Nursing Home", "ssdi": "SSDI",
    "sav": "Sav", "aug": "Aug", "mac": "Mac", "macon": "Mac", "col": "Col", "beau": "Beau",
    "aik": "Aik", "ala": "Ala", "alb": "Alb", "albany": "Alb", "atl": "Atl", "chas": "Chas",
    "fl": "FL", "sc": "SC", "tn": "TN", "nc": "NC", "ky": "KY", "al": "AL",
}


def case_type(project_name):
    """'Jane Doe - Minor - MVA - 9/15/2024' -> 'Minor/MVA'. Keeps only known terms."""
    parts = [p.strip().lower() for p in re.split(r"\s*-\s*", project_name or "")]
    return "/".join(CASE_TERMS[p] for p in parts[1:] if p in CASE_TERMS)


def export_projects(c, full=False):
    print("Projects ...")
    os.makedirs(OUT, exist_ok=True)
    fields = None if full else (
        "projectId,createdDate,projectTypeCode,phaseName,phaseDate,isArchived,"
        "hashtags,projectName,firstPrimaryUsername,lastActivity,incidentDate"
    )
    path = os.path.join(OUT, "projects_full.jsonl" if full else "projects_deidentified.csv")
    n = 0
    with open(path, "w", newline="") as f:
        w = None if full else csv.writer(f)
        if w:
            w.writerow(["projectId", "createdDate", "typeCode", "caseType", "phase", "phaseDate",
                        "archived", "hashtags", "primaryUser", "lastActivity", "incidentDate"])
        for p in c.paged("/projects", requestedFields=fields, sortBy="createdDate", orderBy="asc"):
            n += 1
            if full:
                f.write(json.dumps(p, default=str) + "\n")
                continue
            w.writerow([
                native(p.get("projectId")), (p.get("createdDate") or "")[:10], p.get("projectTypeCode"),
                case_type(p.get("projectName")), p.get("phaseName"), (p.get("phaseDate") or "")[:10],
                p.get("isArchived"), "|".join(h.lstrip("#") for h in p.get("hashtags") or []),
                p.get("firstPrimaryUsername"), (p.get("lastActivity") or "")[:10],
                (p.get("incidentDate") or "")[:10],
            ])
    print(f"  wrote {path} ({n} projects)")


MARKETING_FIELDS = {
    "intake": ["marketingSource", "accidenttype", "resolution", "dateofintake",
               "datecontractsigned", "mikeReferral", "typeOfReferral"],
    "marketing": ["howDidYouHearAboutUs"],
}


def export_marketing(c, since):
    """One row per PI case created on/after `since`: channel attribution + outcome, no client names."""
    print(f"Marketing attribution since {since} ...")
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"marketing_attribution_since_{since}.csv")
    cols = [f for fs in MARKETING_FIELDS.values() for f in fs]
    n = 0
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["projectId", "createdDate", "caseType", "phase", "hashtags", "primaryUser"] + cols)
        for p in c.paged("/projects", createdSince=since, sortBy="createdDate", orderBy="asc",
                         requestedFields="projectId,createdDate,projectTypeCode,phaseName,hashtags,projectName,firstPrimaryUsername"):
            if p.get("projectTypeCode") != "PII":
                continue
            pid = native(p["projectId"])
            row = [pid, (p.get("createdDate") or "")[:10], case_type(p.get("projectName")), p.get("phaseName"),
                   "|".join(h.lstrip("#") for h in p.get("hashtags") or []), p.get("firstPrimaryUsername")]
            for sel, fields in MARKETING_FIELDS.items():
                try:
                    form = c.get(f"/projects/{pid}/forms/{sel}", requestedFields=",".join(fields)) or {}
                except urllib.error.HTTPError:
                    form = {}
                for fld in fields:
                    v = form.get(fld)
                    row.append("|".join(map(str, v)) if isinstance(v, list) else ("" if v is None else v))
            w.writerow(row)
            n += 1
            if n % 250 == 0:
                print(f"  {n} cases ...")
    print(f"  wrote {path} ({n} cases)")


CASE_LISTS = [
    ("contacts", "/projects/{pid}/contacts", {}),
    ("notes", "/projects/{pid}/notes", {}),  # all activity: notes, tasks, emails, texts, calls
    ("appointments", "/projects/{pid}/appointments", {}),
    ("deadlines", "/projects/{pid}/deadlines", {}),
    ("deadlinechains", "/projects/{pid}/deadlinechains", {}),
    ("documents", "/documents", {"projectId": "{pid}"}),
]
_sections_cache = {}
_sections_lock = threading.Lock()


def type_sections(c, tid):
    with _sections_lock:
        if tid not in _sections_cache:
            _sections_cache[tid] = list(c.paged(f"/projecttypes/{tid}/sections"))
        return _sections_cache[tid]


def case_record(c, pid):
    """Everything on one case except the document files themselves."""
    out = {"project": c.get(f"/projects/{pid}")}
    for key, path, params in CASE_LISTS:
        try:
            out[key] = list(c.paged(path.format(pid=pid), **{k: v.format(pid=pid) for k, v in params.items()}))
        except urllib.error.HTTPError as e:
            out[key] = {"error": e.code}
    out["sections"] = {}
    for s in type_sections(c, native(out["project"]["projectTypeId"])):
        sel = s["sectionSelector"]
        try:
            out["sections"][sel] = (list(c.paged(f"/projects/{pid}/collections/{sel}"))
                                    if s.get("isCollection") else c.get(f"/projects/{pid}/forms/{sel}"))
        except urllib.error.HTTPError as e:
            out["sections"][sel] = {"error": e.code}
    return out


def export_case(c, pid):
    print(f"Case {pid} ...")
    save(f"case_{pid}.json", case_record(c, pid))


def export_cases(c, since=None, workers=4):
    folder = os.path.join(OUT, "cases")
    os.makedirs(folder, exist_ok=True)
    err_log = os.path.join(folder, "_errors.log")
    print(f"All cases{' since ' + since if since else ''} -> {folder}/ ({workers} workers)")
    pids = [native(p["projectId"]) for p in c.paged(
        "/projects", requestedFields="projectId", createdSince=since, sortBy="createdDate", orderBy="asc")]
    todo = [pid for pid in pids if not os.path.exists(os.path.join(folder, f"{pid}.json"))]
    print(f"  {len(pids)} cases found, {len(pids) - len(todo)} already saved, {len(todo)} to go")

    def one(pid):
        rec = case_record(c, pid)
        path = os.path.join(folder, f"{pid}.json")
        with open(path + ".tmp", "w") as f:
            json.dump(rec, f, default=str)
        os.replace(path + ".tmp", path)  # only complete files count as saved

    done = failed = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, pid): pid for pid in todo}
        for fut in as_completed(futures):
            try:
                fut.result()
                done += 1
            except Exception as e:  # keep going; failed cases retry on the next run
                failed += 1
                with open(err_log, "a") as f:
                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{futures[fut]}\t{e!r}\n")
            n = done + failed
            if n % 100 == 0 or n == len(todo):
                rate = n / max(time.time() - started, 1)
                left = (len(todo) - n) / rate / 3600 if rate else 0
                print(f"  {n}/{len(todo)} ({failed} failed), about {left:.1f} h left")
    print(f"  done: {done} saved, {failed} failed" + (f" (see {err_log}; rerun to retry)" if failed else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["config", "projects", "marketing", "case", "cases", "all"])
    ap.add_argument("project_id", nargs="?")
    ap.add_argument("--full", action="store_true", help="projects: keep every field, including client names")
    ap.add_argument("--since", help="marketing/cases: first created date, YYYY-MM-DD (marketing defaults to 2024-10-01)")
    ap.add_argument("--workers", type=int, default=4, help="cases: parallel requests (default 4)")
    a = ap.parse_args()
    c = Client()
    print(f"Org {c.org_id}, user {c.user_id}, output -> {OUT}/")
    if a.what in ("config", "all"):
        export_config(c)
    if a.what in ("projects", "all"):
        export_projects(c, full=a.full)
    if a.what == "marketing":
        export_marketing(c, a.since or "2024-10-01")
    if a.what == "cases":
        export_cases(c, a.since, a.workers)
    if a.what == "case":
        if not a.project_id:
            sys.exit("case needs a project id")
        export_case(c, a.project_id)


if __name__ == "__main__":
    main()
