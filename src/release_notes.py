import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List


def _run_git_log(repo: Path, max_count: int, from_ref: str | None, to_ref: str | None) -> str:
    cmd = [
        "git",
        "log",
        "--name-only",
        f"--max-count={max_count}",
        "--date=short",
        "--pretty=format:%x1e%H%x1f%ad%x1f%s%x1f%an",
    ]
    if from_ref and to_ref:
        cmd.append(f"{from_ref}..{to_ref}")
    elif from_ref:
        cmd.append(f"{from_ref}..HEAD")
    elif to_ref:
        cmd.append(to_ref)

    return subprocess.check_output(cmd, cwd=repo, text=True)


def _parse_commits(raw: str) -> List[Dict[str, object]]:
    commits: List[Dict[str, object]] = []
    for block in raw.split("\x1e"):
        block = block.strip("\n")
        if not block:
            continue
        lines = block.splitlines()
        if not lines:
            continue
        meta = lines[0].split("\x1f")
        if len(meta) < 4:
            continue
        commit_hash, date_str, subject, author = meta[:4]
        files = [line.strip() for line in lines[1:] if line.strip()]
        commits.append(
            {
                "hash": commit_hash,
                "date": date_str,
                "subject": subject,
                "author": author,
                "files": files,
            }
        )
    return commits


def _group_by_date(commits: List[Dict[str, object]]) -> Dict[str, List[Dict[str, object]]]:
    grouped: Dict[str, List[Dict[str, object]]] = {}
    for commit in commits:
        day = str(commit["date"])
        grouped.setdefault(day, []).append(commit)
    return grouped


def _format_files(files: List[str], max_items: int = 8) -> str:
    if not files:
        return "none"
    shown = files[:max_items]
    rendered = ", ".join(f"`{f}`" for f in shown)
    if len(files) > max_items:
        rendered += f", +{len(files) - max_items} more"
    return rendered


def _build_markdown(commits: List[Dict[str, object]], max_files: int) -> str:
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    lines = [
        "# Release Notes",
        "",
        f"Generated: {generated_at}",
        "",
        "This file is generated from git history.",
        "",
    ]

    if not commits:
        lines.extend(["No commits found for the selected range.", ""])
        return "\n".join(lines)

    grouped = _group_by_date(commits)
    for day in sorted(grouped.keys(), reverse=True):
        lines.append(f"## {day}")
        lines.append("")
        for commit in grouped[day]:
            short_hash = str(commit["hash"])[:8]
            subject = str(commit["subject"])
            files = list(commit["files"])  # type: ignore[arg-type]
            lines.append(f"### {subject}")
            lines.append(f"- Commit: `{short_hash}`")
            lines.append(f"- Files: {_format_files(files, max_items=max_files)}")
            lines.append("")
    return "\n".join(lines)


def generate_release_notes(
    repo: Path,
    output_path: Path,
    max_count: int,
    max_files: int,
    from_ref: str | None,
    to_ref: str | None,
) -> None:
    raw = _run_git_log(repo=repo, max_count=max_count, from_ref=from_ref, to_ref=to_ref)
    commits = _parse_commits(raw)
    output_path.write_text(_build_markdown(commits, max_files=max_files), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate release notes from git history.")
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Repository path (default: project root)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("RELEASE_NOTES.md"),
        help="Output markdown path (default: RELEASE_NOTES.md in repo root)",
    )
    parser.add_argument(
        "--max-count",
        type=int,
        default=200,
        help="Maximum number of commits to include",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=8,
        help="Maximum number of changed files shown per commit",
    )
    parser.add_argument("--from-ref", help="Start git ref (exclusive), e.g. v0.1.0")
    parser.add_argument("--to-ref", help="End git ref (inclusive), default HEAD")
    args = parser.parse_args()

    repo = args.repo.resolve()
    output = args.output if args.output.is_absolute() else repo / args.output
    generate_release_notes(
        repo=repo,
        output_path=output,
        max_count=args.max_count,
        max_files=args.max_files,
        from_ref=args.from_ref,
        to_ref=args.to_ref,
    )
    print(f"[OK] Wrote release notes to {output}")
