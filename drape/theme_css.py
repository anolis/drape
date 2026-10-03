"""Pure stylesheet checks shared by the desktop app and headless archive worker."""

import re

NEW_DIALOG_RE = re.compile(r"(^|[\s,}>])\.(dialog|prompt-dialog)\b", re.M)
OLD_DIALOG_RE = re.compile(r"(^|[\s,}>])\.modal-dialog\b", re.M)


def cinnamon_css_imports(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return re.findall(r"@import\s+(?:url\(\s*)?[\"']([^\"']+)[\"']", css)


def cinnamon_css_outdated(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return not NEW_DIALOG_RE.search(css)
