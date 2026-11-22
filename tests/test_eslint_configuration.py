"""A policy's ESLint data: the eslint configuration format.

The rule file is TOML with four keys, which configure writes as canonical
JSON for the fixed loader of an ESLint definition. A team policy runs an
ESLint of its own here, whose parser and plugin policy.toml pins.
"""

import pytest
from gateseal.diagnostics import GatesealError
from gateseal.policy import review_policy
from gateseal.tools import load_definition
from gateseal.tools.configurations import FORMATS, ConfigurationFormat
from gateseal.tools.model import Origin
from test_team_tools import team

ESLINT = FORMATS[ConfigurationFormat.ESLINT]
FIELD = "The eslint configuration"
PINNED_AT = "capabilities.typescript.tools.eslint.packages"
PACKAGES = {
    "@typescript-eslint/eslint-plugin": "8.73.0",
    "@typescript-eslint/parser": "8.73.0",
    "typescript": "6.0.3",
}

RULES = """\
# The files ESLint checks.
files = ["src/**/*.ts", "**/*.tsx"]
parser = "@typescript-eslint/parser"

[plugins]
"@typescript-eslint" = "@typescript-eslint/eslint-plugin"

[rules]
no-debugger = "warn"
eqeqeq = ["error", "always", { null = "ignore" }]
"@typescript-eslint/no-unused-vars" = ["error", { argsIgnorePattern = "^_" }]
"""

RENDERED = """\
{
  "files": [
    "src/**/*.ts",
    "**/*.tsx"
  ],
  "parser": "@typescript-eslint/parser",
  "plugins": {
    "@typescript-eslint": "@typescript-eslint/eslint-plugin"
  },
  "rules": {
    "@typescript-eslint/no-unused-vars": [
      "error",
      {
        "argsIgnorePattern": "^_"
      }
    ],
    "eqeqeq": [
      "error",
      "always",
      {
        "null": "ignore"
      }
    ],
    "no-debugger": "warn"
  }
}
"""


def check(text, packages=PACKAGES):
    ESLINT.check(text.encode(), FIELD, packages, PINNED_AT)


def refusal(text, packages=PACKAGES):
    with pytest.raises(GatesealError) as failure:
        check(text, packages)
    return failure.value


def test_configure_writes_the_data_as_canonical_json():
    check(RULES)
    assert ESLINT.render(RULES.encode(), {}).decode() == RENDERED


def test_data_without_parser_or_plugins_still_names_the_plugins():
    core = 'files = ["*.js"]\n\n[rules]\nno-var = "error"\n'
    check(core, {})
    assert ESLINT.render(core.encode(), {}) == (
        b'{\n  "files": [\n    "*.js"\n  ],\n  "plugins": {},\n  "rules": {\n'
        b'    "no-var": "error"\n  }\n}\n'
    )


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            "[plugins]",
            'extends = ["eslint:recommended"]\n\n[plugins]',
            f"{FIELD} sets extends, but it holds only files, parser, plugins and "
            "rules, so no key loads another file.",
        ),
        (
            "[plugins]",
            "[languageOptions.parserOptions]\nproject = true\n\n[plugins]",
            f"{FIELD} sets languageOptions, but it holds only files, parser, "
            "plugins and rules, so no key loads another file.",
        ),
        ('files = ["src/**/*.ts", "**/*.tsx"]\n', "", f"{FIELD} needs files."),
        ("[rules]", "[other]", f"{FIELD} sets other, but it holds only"),
        ('["src/**/*.ts", "**/*.tsx"]', "[]", f"{FIELD}'s files must list one"),
        ('["src/**/*.ts", "**/*.tsx"]', '["src", ""]', f"{FIELD}'s files must list"),
        ('["src/**/*.ts", "**/*.tsx"]', '"src"', f"{FIELD}'s files must be an array."),
        (
            'parser = "@typescript-eslint/parser"',
            'parser = "@babel/eslint-parser"',
            f"{FIELD} names the parser @babel/eslint-parser, which {PINNED_AT} does "
            "not pin.",
        ),
        (
            'parser = "@typescript-eslint/parser"',
            "parser = 1",
            f"{FIELD}'s parser must be a nonempty printable string.",
        ),
        (
            '"@typescript-eslint" = "@typescript-eslint/eslint-plugin"',
            'react = "eslint-plugin-react"',
            f"{FIELD} names the plugin eslint-plugin-react for react, which "
            f"{PINNED_AT} does not pin.",
        ),
        (
            '"@typescript-eslint" = "@typescript-eslint/eslint-plugin"',
            '"React" = "typescript"',
            f"{FIELD} declares the plugin prefix 'React', but a prefix is a rule "
            "name's start",
        ),
        (
            '"@typescript-eslint" = "@typescript-eslint/eslint-plugin"',
            '"@typescript-eslint" = ["@typescript-eslint/eslint-plugin"]',
            f"{FIELD}'s plugins.@typescript-eslint must be a nonempty printable",
        ),
        (
            'no-debugger = "warn"',
            'no-debugger = "warn"\n"react/jsx-key" = "error"',
            f"{FIELD} sets the rule react/jsx-key, but its plugins declare no react.",
        ),
        (
            'no-debugger = "warn"',
            'no-debugger = "fatal"',
            f"{FIELD} sets the rule no-debugger without a severity; a rule takes "
            "off, warn or error, then its options.",
        ),
        ('no-debugger = "warn"', "no-debugger = []", f"{FIELD} sets the rule"),
        ('no-debugger = "warn"', "no-debugger = 2", f"{FIELD} sets the rule"),
        (
            '{ null = "ignore" }',
            "{ since = 2026-01-01 }",
            f"{FIELD}'s rules.eqeqeq holds 2026-01-01, which JSON cannot hold; "
            "ESLint data holds strings, finite numbers, booleans, arrays and tables.",
        ),
        ('{ null = "ignore" }', "inf", f"{FIELD}'s rules.eqeqeq holds inf,"),
        ('{ null = "ignore" }', "[{ x = nan }]", f"{FIELD}'s rules.eqeqeq holds nan,"),
        ('{ null = "ignore" }', "{ at = 10:30:00 }", f"{FIELD}'s rules.eqeqeq holds"),
        ("\n[rules]", "\r\n[rules]", f"{FIELD} must use LF line endings, without CR."),
    ],
    ids=[
        "extends",
        "language options",
        "no files",
        "no rules",
        "empty files",
        "empty pattern",
        "files string",
        "unpinned parser",
        "parser number",
        "unpinned plugin",
        "prefix",
        "plugin array",
        "undeclared prefix",
        "severity",
        "empty rule",
        "numeric severity",
        "date",
        "infinite number",
        "nan in a table",
        "time",
        "carriage return",
    ],
)
def test_data_that_could_load_or_break_something_is_refused(old, new, message):
    assert old in RULES
    failure = refusal(RULES.replace(old, new, 1))
    assert failure.code == "invalid_field"
    assert str(failure).startswith(message)


@pytest.mark.parametrize(
    "value",
    ['"off"', '["warn"]', '["error", 1, 2.5, true, ["a"], { b = { c = "d" } }]'],
)
def test_a_rule_takes_a_severity_then_any_json_options(value):
    check(RULES.replace('no-debugger = "warn"', f"no-debugger = {value}"))


@pytest.mark.parametrize(
    ("prefix", "taken"),
    [
        ("@next/next", True),
        ("@typescript-eslint", True),
        ("react", True),
        ("react/hooks", False),
        ("@next/next/core", False),
        ("@next/@next", False),
    ],
)
def test_a_plugin_prefix_is_a_name_or_a_scoped_name(prefix, taken):
    text = (
        f'files = ["*.ts"]\n\n[plugins]\n"{prefix}" = "eslint-plugin-x"\n\n'
        f'[rules]\n"{prefix}/rule" = "error"\n'
    )
    if taken:
        check(text, {"eslint-plugin-x": "1.0.0"})
    else:
        assert "declares the plugin prefix" in str(
            refusal(text, {"eslint-plugin-x": "1.0.0"})
        )


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ('files = ["a"]\nrules = 1\n', "rules"),
        ('files = ["a"]\nplugins = 1\n[rules]\n', "plugins"),
    ],
)
def test_rules_and_plugins_are_tables(text, key):
    assert str(refusal(text)) == f"{FIELD}'s {key} must be an object."


def test_an_unparsable_file_is_invalid_toml():
    assert refusal('files = ["a"\n').code == "invalid_toml"


DEFINITION = """\
kind = "npm-package"
stages = ["commit", "run", "ci"]
scope = "files"

[source]
package = "eslint"

[executable]
name = "eslint"

[configuration]
format = "eslint"

[files.loader]
extension = "mjs"
content = "export default [];\\n"

[hooks.lint]
name = "lint"
args = ["--config", "{loader}", "--"]
types_or = ["ts"]
output = "plain"
"""

CHOICE = '[choices.style]\nkey = "style"\nsince = { strict = "1.0.0" }\n\n[source]'


def test_the_data_goes_to_a_json_output_beside_the_loader():
    lint = load_definition("lint", DEFINITION.encode(), Origin.TEAM)
    assert lint.owned == (".gateseal/lint.json", ".gateseal/lint-loader.mjs")
    assert lint.hooks["lint"].args == ("--config", ".gateseal/lint-loader.mjs", "--")


def test_an_eslint_configuration_takes_no_choices():
    edited = DEFINITION.replace("[source]", CHOICE).encode()
    with pytest.raises(GatesealError) as failure:
        load_definition("lint", edited, Origin.TEAM)
    assert failure.value.code == "invalid_field"
    assert str(failure.value) == (
        "tools.lint.choices needs a toml configuration for its choices to render into."
    )


POLICY = """\
schema = 5
release = "team-1"
minimum_compiler = "0.2.0"
node = "24.22.3"

[capabilities.typescript.tools.lint]
configuration = "rules/lint.toml"
pin = "10.15.0"
reviewed = ["10.15.0"]

[capabilities.typescript.tools.lint.packages]
"@typescript-eslint/parser" = "8.73.0"
"""


def reviewed(tmp_path, rules):
    root = team(
        tmp_path, POLICY, {"tools/lint.toml": DEFINITION, "rules/lint.toml": rules}
    )
    return review_policy(lambda name: (root / name).read_bytes())


def test_review_checks_the_data_against_the_packages_of_policy_toml(tmp_path):
    core = 'files = ["**/*.ts"]\nparser = "@typescript-eslint/parser"\n\n[rules]\n'
    assert reviewed(tmp_path, core).uses["lint"].configuration == core.encode()
    plugin = core.replace(
        "\n\n", '\nplugins = { ts = "@typescript-eslint/eslint-plugin" }\n\n'
    )
    with pytest.raises(GatesealError) as failure:
        reviewed(tmp_path, plugin)
    assert str(failure.value) == (
        "The lint configuration names the plugin @typescript-eslint/eslint-plugin "
        "for ts, which capabilities.typescript.tools.lint.packages does not pin."
    )
