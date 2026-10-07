"""NSO acquisition commands. Run from the project root; see --help."""
import argparse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in [("catalog", "Discover PX-Web tables"), ("web", "Acquire Website releases"), ("qc", "Check PX-Web quality"), ("profile", "Export corpus profiles"), ("validate", "Validate stored artifacts")]:
        commands.add_parser(name, help=help_text)
    for name in ("api", "html"):
        command = commands.add_parser(name, help=f"Acquire PX-Web {name.upper()} tables")
        command.add_argument("--mode", choices=("resume", "refresh"), default="resume")
        command.add_argument("--database")
        command.add_argument("--table-id")
    args = parser.parse_args()
    if args.command == "catalog":
        from src.catalog import build_classified_catalog
        from src.paths import PXWEB_CATALOG_PATH
        catalog = build_classified_catalog(output_path=PXWEB_CATALOG_PATH, timeout=30, delay=0.05, verbose=True)
        print(f"Catalog saved: {PXWEB_CATALOG_PATH}\nTables: {len(catalog):,}")
        print(catalog["source"].value_counts().to_string())
    elif args.command in ("api", "html"):
        options = dict(mode=args.mode, database=args.database, table_id=args.table_id)
        if args.command == "api":
            from src.px_api import run_api_download
            log = run_api_download(max_retries=3, **options)
        else:
            from src.px_html import run_html_download
            log = run_html_download(timeout=90, max_attempts=3, **options)
        print(log["status"].value_counts(dropna=False).to_string())
    elif args.command == "qc":
        from src.qc import run_pxweb_qc
        api, html = run_pxweb_qc(save_reports=True)
        for name, frame in (("API", api), ("HTML", html)):
            print(f"\n{name} QC:")
            print(frame["status"].value_counts(dropna=False).to_string())
    else:
        from importlib import import_module
        import_module(f"src.{args.command}").main()


if __name__ == "__main__":
    main()
