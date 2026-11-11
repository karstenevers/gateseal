"""Build the docs site from pages written to read well on GitHub.

Each page links to other pages, and to any file in the repository, by its
relative path in the repository. These hooks put the root pages in ROOT_PAGES
on the site, point every link at its site page or at the file on GitHub,
render `mermaid` code blocks as diagrams, show theme images for the site's
palette, and use the canonical URL that Read the Docs sets.
"""

from __future__ import annotations

import logging
import os
import posixpath
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from xml.etree.ElementTree import Element

from markdown import Extension, Markdown
from markdown.treeprocessors import Treeprocessor
from mkdocs.config.defaults import MkDocsConfig
from mkdocs.structure.files import File, Files
from mkdocs.structure.pages import Page
from pymdownx.superfences import fence_code_format

ROOT_PAGES = {"GLOSSARY.md": "glossary.md", "VISION.md": "vision.md"}
# Links that leave this repository and render as plain text. An entry that no
# page uses fails the build, so the list only shrinks.
OUTSIDE_LINKS: tuple[str, ...] = ()
LINK_ATTRIBUTES = {"a": "href", "img": "src"}
# GitHub picks a theme image by the system theme. The site shows both images and
# Material hides the one for the other palette, so the palette toggle works.
THEME_PICTURE = re.compile(
    r'<picture>\s*<source media="\(prefers-color-scheme: dark\)"'
    r' srcset="(?P<dark>[^"]+)">\s*'
    r'<img (?P<before>[^>]*)src="(?P<light>[^"]+)"(?P<after>[^>]*)>\s*</picture>'
)

log = logging.getLogger("mkdocs.hooks.repository_links")


class RepositoryLinks(Extension):
    def __init__(self, config: MkDocsConfig) -> None:
        super().__init__()
        self.root = Path(config.config_file_path).resolve().parent
        self.docs = Path(config.docs_dir).relative_to(self.root).as_posix()
        self.blob = f"{(config.repo_url or '').rstrip('/')}/blob/main/"
        self.root_pages = {
            source: site
            for source, site in ROOT_PAGES.items()
            if (self.root / source).is_file()
        }
        self.sources = {site: source for source, site in self.root_pages.items()}
        self.outside_used: set[str] = set()
        self.files = Files([])
        self.page: File | None = None

    def extendMarkdown(self, md: Markdown) -> None:
        # MkDocs resolves links at priority 0, so this rewrite runs first.
        md.treeprocessors.register(_Rewrite(md, self), "repository_links", 2)

    def source(self, file: File) -> str:
        return self.sources.get(file.src_uri) or f"{self.docs}/{file.src_uri}"

    def site(self, path: str) -> str | None:
        if path in self.root_pages:
            return self.root_pages[path]
        prefix = f"{self.docs}/"
        return path.removeprefix(prefix) if path.startswith(prefix) else None

    def outside(self, target: str) -> bool:
        for entry in OUTSIDE_LINKS:
            if target == entry or (entry.endswith("/") and target.startswith(entry)):
                self.outside_used.add(entry)
                return True
        return False

    def resolve(self, url: str) -> str | None:
        scheme, netloc, path, query, fragment = urlsplit(url)
        if scheme or netloc or not path or path.startswith("/") or not self.page:
            return url
        source = self.source(self.page)
        target = posixpath.normpath(posixpath.join(posixpath.dirname(source), path))
        site = self.site(target)
        if site is None:
            if target.startswith("../"):
                return None if self.outside(target) else url
            if not (self.root / target).exists():
                log.warning(f"'{source}' links to '{target}', which does not exist.")
            return urlunsplit(("", "", self.blob + target, query, fragment))
        linked = self.files.get_file_from_path(site)
        if linked and linked.inclusion.is_excluded():
            log.warning(f"'{source}' links to '{target}', which is not on the site.")
        here = posixpath.dirname(self.page.src_uri) or "."
        return urlunsplit(("", "", posixpath.relpath(site, here), query, fragment))


class _Rewrite(Treeprocessor):
    def __init__(self, md: Markdown, links: RepositoryLinks) -> None:
        super().__init__(md)
        self.links = links

    def run(self, root: Element) -> None:
        for element in root.iter():
            key = LINK_ATTRIBUTES.get(element.tag)
            if key and (url := element.get(key)):
                resolved = self.links.resolve(url)
                if resolved is None:
                    element.tag = "span"
                    del element.attrib[key]
                else:
                    element.set(key, resolved)


_links: RepositoryLinks | None = None


def on_config(config: MkDocsConfig) -> MkDocsConfig:
    global _links
    _links = RepositoryLinks(config)
    config.markdown_extensions.append(_links)
    fences = config.mdx_configs.setdefault("pymdownx.superfences", {})
    fences["custom_fences"] = [
        {"name": "mermaid", "class": "mermaid", "format": fence_code_format}
    ]
    config.site_url = os.environ.get("READTHEDOCS_CANONICAL_URL", config.site_url)
    return config


def on_files(files: Files, config: MkDocsConfig) -> Files:
    assert _links is not None
    for source, site in _links.root_pages.items():
        if files.get_file_from_path(site):
            log.warning(f"'{source}' and '{_links.docs}/{site}' are both '{site}'.")
        page = File.generated(config, site, abs_src_path=str(_links.root / source))
        page.edit_uri = posixpath.relpath(source, _links.docs)
        files.append(page)
    _links.files = files
    return files


def on_page_markdown(
    markdown: str, page: Page, config: MkDocsConfig, files: Files
) -> str:
    assert _links is not None
    _links.page = page.file
    return markdown


def on_page_content(html: str, page: Page, config: MkDocsConfig, files: Files) -> str:
    # A page without sections has nothing for the contents sidebar to list.
    if not any(link.level > 1 or link.children for link in page.toc):
        page.meta.setdefault("hide", []).append("toc")
    return THEME_PICTURE.sub(_theme_images, html)


def _theme_images(picture: re.Match[str]) -> str:
    before, after = picture["before"], picture["after"]
    light = f'<img {before}src="{picture["light"]}#only-light"{after}>'
    return light + f'<img {before}src="{picture["dark"]}#only-dark"{after}>'


def on_post_build(config: MkDocsConfig) -> None:
    assert _links is not None
    for entry in OUTSIDE_LINKS:
        if entry not in _links.outside_used:
            log.warning(f"No page links to '{entry}'. Remove it from OUTSIDE_LINKS.")
