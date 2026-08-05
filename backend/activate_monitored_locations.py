#!/usr/bin/env python3
"""
Activate / deactivate Makro branches (makro_locations.is_active) so the
Price-by-Location updater will (or won't) scrape them.

The updater only scrapes branches that are BOTH monitored and active, so after
(re)seeding makro_locations you often need to re-activate the ones you want.
Idempotent — safe to run repeatedly.

Pick which branches, and whether to turn them on or off:

    # Activate all branches currently in pbl_monitored_locations (default)
    python activate_monitored_locations.py

    # Activate specific branches by branch_code (postcode in the base schema)
    python activate_monitored_locations.py --code 10240 50000 93180

    # Activate specific branches by makro_locations.id
    python activate_monitored_locations.py --id 610 751

    # Deactivate instead of activate (works with any selection)
    python activate_monitored_locations.py --deactivate --code 10240
    python activate_monitored_locations.py --deactivate            # the monitored ones
    python activate_monitored_locations.py --deactivate --all      # every branch (reset)

    # Preview only / list current state
    python activate_monitored_locations.py --code 10240 --dry-run
    python activate_monitored_locations.py --list-active

Reads DB creds from backend/.env (DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD/DB_SSLMODE).
"""
import argparse
import os
import sys
import psycopg2
from psycopg2.extras import RealDictCursor

# Load backend/.env
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
env = {}
if os.path.exists(_env_path):
    with open(_env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env.setdefault(k.strip(), v.strip())


def get_conn():
    return psycopg2.connect(
        host=env.get("DB_HOST", "localhost"),
        port=int(env.get("DB_PORT", 5432)),
        dbname=env.get("DB_NAME", "pricehawk"),
        user=env.get("DB_USER", "postgres"),
        password=env.get("DB_PASSWORD", ""),
        sslmode=env.get("DB_SSLMODE", "prefer"),
        cursor_factory=RealDictCursor,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Activate/deactivate Makro branches for scraping.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--code", nargs="+", metavar="BRANCH_CODE",
                   help="Select branches by branch_code (postcode).")
    g.add_argument("--id", nargs="+", type=int, metavar="ID",
                   help="Select branches by makro_locations.id.")
    g.add_argument("--all", action="store_true",
                   help="Select ALL branches (use with --deactivate to reset).")
    ap.add_argument("--deactivate", action="store_true",
                    help="Set is_active = FALSE instead of TRUE.")
    ap.add_argument("--dry-run", action="store_true", help="Show what would change, don't write.")
    ap.add_argument("--list-active", action="store_true",
                    help="Just list currently active branches and exit.")
    args = ap.parse_args()

    target = not args.deactivate           # TRUE = activate, FALSE = deactivate
    action = "deactivating" if args.deactivate else "activating"
    active_word = "active" if not args.deactivate else "inactive"

    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor()
    try:
        if args.list_active:
            cur.execute("""SELECT id, branch_code, name FROM makro_locations
                           WHERE is_active = TRUE ORDER BY name""")
            rows = cur.fetchall()
            print(f"Currently active: {len(rows)} branch(es)")
            for r in rows:
                print(f"  id={r['id']}  branch_code={r['branch_code']}  {r['name']}")
            return 0

        # Build the WHERE clause for the chosen selection
        if args.code:
            where, params = "branch_code = ANY(%s)", (args.code,)
            selection = f"branch_code in {args.code}"
        elif args.id:
            where, params = "id = ANY(%s)", (args.id,)
            selection = f"id in {args.id}"
        elif args.all:
            where, params = "TRUE", ()
            selection = "ALL branches"
        else:
            where = "id IN (SELECT location_id FROM pbl_monitored_locations)"
            params = ()
            selection = "monitored locations"

        # Preview what matches
        cur.execute(f"SELECT id, branch_code, name, is_active FROM makro_locations WHERE {where} ORDER BY name", params)
        matched = cur.fetchall()
        if not matched:
            print(f"No branches matched ({selection}). Nothing to do.")
            return 0

        print(f"Matched {len(matched)} branch(es) [{selection}] — {action}:")
        for r in matched:
            if r["is_active"] == target:
                flag = f"already {active_word}"
            else:
                flag = f"-> {action}"
            print(f"  id={r['id']}  branch_code={r['branch_code']}  {r['name']}  ({flag})")

        if args.dry_run:
            print("\n[dry-run] no changes written.")
            return 0

        cur.execute(f"UPDATE makro_locations SET is_active = %s WHERE {where}", (target, *params))
        conn.commit()
        print(f"\nDone. {cur.rowcount} row(s) set is_active = {target}.")
        return 0
    except Exception as e:
        conn.rollback()
        print(f"ROLLBACK due to error: {e}", file=sys.stderr)
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
