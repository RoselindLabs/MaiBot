"""Archive one clean source commit and smoke-import the extracted source."""

from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if git("status", "--porcelain").strip():
        raise RuntimeError("Source package requires a clean exact commit")
    output = args.output.resolve()
    if output.is_relative_to(ROOT) or output.exists():
        raise RuntimeError("Choose a new artifact path outside the checkout")
    head = git("rev-parse", "HEAD").decode().strip()
    manifest = json.loads((ROOT / "neko-patches.json").read_text(encoding="utf-8"))
    git("merge-base", "--is-ancestor", manifest["upstream_commit"], head)
    # Only committed public files, never a recursive copy of a runtime directory.
    git("archive", "--format=zip", "--output=" + str(output), head)
    with TemporaryDirectory(prefix="maibot-neko-source-") as temporary:
        source = Path(temporary) / "source"
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            for name in names:
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts or "\\" in name:
                    raise RuntimeError("Unsafe archive member")
                if (
                    path.parts[0] in {"config", "data", ".venv", ".git"}
                    or path.name
                    in {
                        "config.toml",
                        "bot_config.toml",
                        "model_config.toml",
                        ".env",
                    }
                    or path.suffix in {".db", ".sqlite", ".sqlite3"}
                ):
                    raise RuntimeError("Runtime member in source archive: " + name)
            archive.extractall(source)
        for name, digest in manifest["sources"].items():
            if hashlib.sha256((source / name).read_bytes()).hexdigest() != digest:
                raise RuntimeError("Extracted source mismatch: " + name)
        env = dict(
            os.environ,
            MAIBOT_INSTANCE_DIR=str(Path(temporary) / "instance"),
            MAIBOT_LOCALE="zh-CN",
            PYTHONIOENCODING="utf-8",
        )
        program = (
            "from pathlib import Path;import sys;root=Path(sys.argv[1]).resolve();"
            "sys.path.insert(0,str(root/'scripts'));sys.path.insert(0,str(root));"
            "import neko_pytest_guard;import maibot_sdk;"
            "import src.maisaka.builtin_tool.reply as reply;"
            "import src.maisaka.context.compaction as compaction;"
            "import src.chat.replyer.maisaka_generator_base as generator;"
            "import src.plugin_runtime.component_query as query;"
            "from src.plugin_runtime.transport.base import MAX_FRAME_SIZE;"
            "from src.llm_models.gemini_failure_diagnostics import gemini_failure_diagnostic;"
            "assert MAX_FRAME_SIZE == 32 * 1024 * 1024;assert callable(gemini_failure_diagnostic);"
            "from src.common.prompt_i18n import load_prompt_template;"
            "templates=('agent_acp','agent_acp_computer','agent_acp_environment','agent_acp_failure',"
            "'agent_planner','agent_planner_continue','agent_planner_resume','a_memorix_fact_deduplication');"
            "assert all(load_prompt_template(name,locale=locale).template "
            "for name in templates for locale in ('zh-CN','en-US','ja-JP'));"
            "assert all(Path(m.__file__).resolve().is_relative_to(root) for m in [reply,compaction,generator,query]);"
            "assert callable(reply._notify_reply_delivery);assert callable(compaction._neko_compaction_items);"
            "assert callable(query.ComponentQueryService._build_trusted_tool_context);"
            "print('Extracted source imports verified with real SDK; no model or QQ calls')"
        )
        subprocess.run(
            [sys.executable, "-I", "-X", "utf8", "-c", program, str(source)], cwd=temporary, env=env, check=True
        )
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    record = {
        "commit": head,
        "upstream_commit": manifest["upstream_commit"],
        "sha256": digest,
        "artifact": output.name,
        "source_hashes_verified": True,
        "isolated_import": True,
        "relocated_templates_verified": True,
    }
    output.with_suffix(".json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record))


if __name__ == "__main__":
    main()
