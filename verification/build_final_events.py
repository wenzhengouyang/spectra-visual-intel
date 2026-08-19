"""Import-friendly alias for the hyphenated CLI module."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_path = Path(__file__).with_name("build-final-events.py")
_spec = spec_from_file_location("verification._build_final_events_cli", _path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load {_path}")
_module = module_from_spec(_spec)
_spec.loader.exec_module(_module)

build_bundle = _module.build_bundle
validate_review = _module.validate_review
