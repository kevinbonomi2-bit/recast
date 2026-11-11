"""Entry point.

    python -m recast                     start the tray app
    python -m recast ops FILE...         list what the wheels would offer
    python -m recast run OP_ID FILE...   run one operation from the command line
    python -m recast wheel FILE...       open the wheel for files (used by "Send to")
    python -m recast helper PIPE         internal: heavy work for the tray app (see recast/helper.py)
"""

from __future__ import annotations

import sys
from pathlib import Path


def cli(argv: list[str]) -> int:
    from .engine import WHEELS, REGISTRY, ConversionError, Context, operations_for, run

    cmd, rest = argv[0], argv[1:]
    if cmd == "ops":
        files = [Path(a) for a in rest]
        for w in WHEELS:
            print(f"{w}: " + ", ".join(f"{o.label} [{o.id}]" for o in operations_for(files, w)))
        return 0
    if cmd == "run":
        op_id, files = rest[0], [Path(a).resolve() for a in rest[1:]]
        operation = REGISTRY.get(op_id)
        if operation is None:
            print(f"unknown operation {op_id}", file=sys.stderr)
            return 2
        if not operation.accepts(files):
            print(f"{operation.label} doesn't apply to these files", file=sys.stderr)
            return 2

        def show(f: float) -> None:
            print(f"\r{f:6.1%}", end="", flush=True)

        try:
            outs = run(operation, files, Context(on_progress=show))
        except ConversionError as e:
            print(f"\n{e}", file=sys.stderr)
            # The installed Recast.exe has no console, so the log is the only place this shows up.
            from .app import setup_logging
            import logging
            setup_logging()
            logging.getLogger("recast.cli").error("%s failed: %s", op_id, e)
            return 1
        print()
        for o in outs:
            print(o)
        return 0
    print(__doc__)
    return 2


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] in ("ops", "run"):
        return cli(argv)
    if len(argv) == 2 and argv[0] == "helper":
        from .helper import main as helper_main
        return helper_main(argv[1])
    from .app import main as app_main
    return app_main(argv)


if __name__ == "__main__":
    sys.exit(main())
