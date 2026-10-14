# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render the Markdown guide as a website page or a single-file handbook.

Python-Markdown is a development dependency, never part of the extension bundle.
The template's Google Fonts stylesheet remains external in both output modes.
"""

import argparse
import base64
import hashlib
import html
import json
import re
import tomllib
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import markdown

ROOT = Path(__file__).resolve().parents[1]
DOCS_REPOSITORY = "midnitefox/blender-plugin"
DOCS_REF = "feat/provider-choice"
SLOT = re.compile(r"{{\s*([^{}]+?)\s*}}")
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


class HandbookError(ValueError):
    """An input cannot produce a complete, locally reproducible handbook."""


class _Page(HTMLParser):
    def __init__(self, source: Path, root: Path, inline_images: bool):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.root = root
        self.inline_images = inline_images
        self.parts: list[str] = []
        self.assets: dict[Path, bytes] = {}
        self.lang = False
        self.charset = False
        self.viewport = False
        self.title = False
        self.in_title = False
        self.doctype = False

    def _image(self, attrs: dict[str, str | None]) -> None:
        if not (attrs.get("alt") or "").strip():
            raise HandbookError("Every image needs descriptive, nonempty alt text")
        src = attrs.get("src") or ""
        url = urlsplit(src)
        decoded = unquote(url.path)
        relative = Path(decoded)
        if (
            not decoded
            or url.scheme
            or url.netloc
            or url.query
            or url.fragment
            or "\\" in decoded
            or relative.is_absolute()
            or ".." in relative.parts
        ):
            raise HandbookError(f"Image must use a local relative path: {src!r}")
        image = (self.source.parent / relative).resolve()
        if not image.is_relative_to(self.source.parent.resolve()) or not image.is_file():
            raise HandbookError(f"Missing image or path outside the guide directory: {src!r}")
        mime = IMAGE_TYPES.get(image.suffix.lower())
        if mime is None:
            raise HandbookError(f"Unsupported image type: {src!r}")
        data = image.read_bytes()
        if not data:
            raise HandbookError(f"Empty image: {src!r}")
        if self.inline_images:
            attrs["src"] = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        else:
            self.assets[relative] = data
            attrs["src"] = quote(relative.as_posix(), safe="/-._~")
        if "srcset" in attrs:
            raise HandbookError("Image srcset is unsupported; use one explicit local image")

    def _link(self, attrs: dict[str, str | None]) -> None:
        href = attrs.get("href") or ""
        url = urlsplit(href)
        if url.scheme or url.netloc:
            if url.scheme not in ("https", "http", "mailto"):
                raise HandbookError(f"Unsupported link protocol: {href!r}")
            return
        if not url.path:
            return
        decoded = unquote(url.path)
        if "\\" in decoded or Path(decoded).is_absolute():
            raise HandbookError(f"Link must be repository-relative: {href!r}")
        target = (self.source.parent / decoded).resolve()
        if not target.is_relative_to(self.root) or not target.exists():
            raise HandbookError(f"Missing repository link: {href!r}")
        relative = target.relative_to(self.root).as_posix()
        path = "" if relative == "." else "/" + quote(relative, safe="/-._~")
        attrs["href"] = urlunsplit(
            (
                "https",
                "github.com",
                f"/{DOCS_REPOSITORY}/{'tree' if target.is_dir() else 'blob'}/{DOCS_REF}" + path,
                url.query,
                url.fragment,
            )
        )

    def _tag(self, tag: str, pairs: list[tuple[str, str | None]], *, closed: bool) -> None:
        attrs = dict(pairs)
        if len(attrs) != len(pairs):
            raise HandbookError(f"Duplicate attributes on <{tag}>")
        if tag == "img":
            self._image(attrs)
        elif tag == "a":
            self._link(attrs)
        elif tag == "html":
            self.lang = bool((attrs.get("lang") or "").strip())
        elif tag == "meta":
            self.charset |= (attrs.get("charset") or "").lower() == "utf-8"
            self.viewport |= attrs.get("name") == "viewport" and "width=device-width" in (
                attrs.get("content") or ""
            )
        elif tag == "title":
            self.in_title = True
        fields = "".join(
            f' {key}="{html.escape(value, quote=True)}"' if value is not None else f" {key}"
            for key, value in attrs.items()
        )
        self.parts.append(f"<{tag}{fields}{' /' if closed else ''}>")

    def handle_starttag(self, tag, attrs):
        self._tag(tag, attrs, closed=False)

    def handle_startendtag(self, tag, attrs):
        self._tag(tag, attrs, closed=True)

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if self.in_title and data.strip():
            self.title = True
        self.parts.append(data)

    def handle_entityref(self, name):
        self.parts.append(f"&{name};")

    def handle_charref(self, name):
        self.parts.append(f"&#{name};")

    def handle_comment(self, data):
        self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl):
        self.doctype |= decl.lower() == "doctype html"
        self.parts.append(f"<!{decl}>")


def _asset_destination(directory: Path, relative: Path) -> Path:
    candidate = directory
    for part in relative.parts:
        candidate /= part
        if candidate.is_symlink():
            raise HandbookError("Asset destination follows a symlink")
    return candidate.resolve()


def _stale_assets(
    record: Path, destinations: dict, protected: set[Path], source_directory: Path
) -> list[Path]:
    """Only remove unchanged files recorded by this output's previous render."""
    if record.is_symlink() or record in protected:
        raise HandbookError("Asset record would overwrite a handbook input or follow a symlink")
    if not record.exists():
        return []
    if record.stat().st_size > 1024 * 1024:
        raise HandbookError("Handbook asset record is too large")
    previous = json.loads(record.read_text(encoding="utf-8"))
    if (
        not isinstance(previous, dict)
        or previous.get("version") != 1
        or not isinstance(previous.get("files"), dict)
    ):
        raise HandbookError("Invalid handbook asset record")
    stale = []
    for name, digest in previous["files"].items():
        relative = Path(name)
        if (
            not name
            or "\\" in name
            or relative.is_absolute()
            or ".." in relative.parts
            or not isinstance(digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
        ):
            raise HandbookError("Invalid path or digest in handbook asset record")
        candidate = _asset_destination(record.parent, relative)
        if (
            not candidate.is_relative_to(record.parent)
            or candidate in protected
            or candidate == record
            or candidate == (source_directory / relative).resolve()
        ):
            raise HandbookError("Recorded asset escapes output or overlaps a handbook input")
        if candidate in destinations or not candidate.exists():
            continue
        if not candidate.is_file() or hashlib.sha256(candidate.read_bytes()).hexdigest() != digest:
            raise HandbookError(
                "Previously generated asset was modified; inspect it before cleanup"
            )
        stale.append(candidate)
    return stale


def build_handbook(
    source: Path,
    template: Path,
    manifest: Path,
    output: Path,
    *,
    inline_images: bool = False,
    root: Path = ROOT,
) -> Path:
    """Validate all inputs before writing HTML and referenced image snapshots."""
    source, template, manifest, output, root = (
        path.resolve() for path in (source, template, manifest, output, root)
    )
    if output in (source, template, manifest):
        raise HandbookError("Output would overwrite a handbook input")
    layout = template.read_text(encoding="utf-8")
    slots = SLOT.findall(layout)
    if slots.count("content") != 1 or slots.count("toc") != 1 or "version" not in slots:
        raise HandbookError("Template needs one content slot, one toc slot and a version slot")
    if set(slots) - {"content", "toc", "version"}:
        raise HandbookError("Template contains unknown slots")
    version = tomllib.loads(manifest.read_text(encoding="utf-8")).get("version")
    if not isinstance(version, str) or not re.fullmatch(
        r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", version
    ):
        raise HandbookError("Manifest must contain a three-part release version")
    renderer = markdown.Markdown(
        extensions=["fenced_code", "tables", "toc"],
        extension_configs={"toc": {"marker": "", "toc_depth": "2-3"}},
        output_format="html",
    )
    content = renderer.convert(source.read_text(encoding="utf-8"))
    values = {"content": content, "toc": renderer.toc, "version": html.escape(version)}
    page = SLOT.sub(lambda match: values[match[1].strip()], layout)
    parsed = _Page(source, root, inline_images)
    parsed.feed(page)
    parsed.close()
    if not all((parsed.doctype, parsed.lang, parsed.charset, parsed.viewport, parsed.title)):
        raise HandbookError("Template needs doctype, language, UTF-8, viewport and title metadata")
    destinations = {}
    record = output.with_name(output.name + ".assets.json")
    protected = {output, source, template, manifest}
    for relative, data in parsed.assets.items():
        destination = _asset_destination(output.parent, relative)
        if (
            not destination.is_relative_to(output.parent)
            or destination in protected | {record}
            or destination == (source.parent / relative).resolve()
        ):
            raise HandbookError(
                "Image destination would escape output or overwrite a handbook input"
            )
        destinations[destination] = data
    stale = _stale_assets(record, destinations, protected, source.parent)
    output.parent.mkdir(parents=True, exist_ok=True)
    for destination, data in destinations.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    output.write_text("".join(parsed.parts), encoding="utf-8")
    for candidate in stale:
        candidate.unlink()
    if destinations:
        files = {
            item.relative_to(output.parent).as_posix(): hashlib.sha256(data).hexdigest()
            for item, data in destinations.items()
        }
        record.write_text(json.dumps({"version": 1, "files": files}, indent=2) + "\n")
    elif record.exists():
        record.unlink()
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "docs/USER_GUIDE.md")
    parser.add_argument("--template", type=Path, default=ROOT / "docs/handbook-template.html")
    parser.add_argument("--manifest", type=Path, default=ROOT / "scenario/blender_manifest.toml")
    parser.add_argument("--output", type=Path, default=ROOT / "site/index.html")
    parser.add_argument("--inline-images", action="store_true")
    args = parser.parse_args(argv)
    try:
        output = build_handbook(
            args.source, args.template, args.manifest, args.output, inline_images=args.inline_images
        )
    except (OSError, ValueError) as error:
        parser.exit(1, f"Handbook error: {error}\n")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
