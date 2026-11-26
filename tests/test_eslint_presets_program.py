"""The program that gateseal policy lock runs on Node to read ESLint presets.

It runs on the bundled policy's Node over stand-in packages: an ESLint whose
built-in rules mark some recommended, and a plugin with flat, legacy and
typed configs.
"""

import json
import os

import pytest
from conftest import on_machine
from gateseal import npm_tool
from gateseal.policy import load_bundled_policy
from gateseal.tools.eslint_presets import PROGRAM
from gateseal.tools.mise import archives
from installation import success

pytestmark = pytest.mark.integration

PACKAGES = {
    "eslint/package.json": {
        "name": "eslint",
        "exports": {"./use-at-your-own-risk": "./unsupported.js"},
    },
    "eslint/unsupported.js": """\
const rule = (docs) => ({ meta: { docs } });
module.exports = {
  builtinRules: new Map([
    ["no-var", rule({ recommended: true })],
    ["no-undef", rule({ recommended: true })],
    ["eqeqeq", rule({})],
  ]),
};
""",
    "eslint-plugin-x/package.json": {"name": "eslint-plugin-x", "main": "index.js"},
    "eslint-plugin-x/index.js": """\
module.exports = {
  rules: {
    typed: { meta: { docs: { requiresTypeChecking: true } } },
    plain: { meta: { docs: {} } },
  },
  configs: {
    "flat/a": [
      { name: "x/base", languageOptions: {}, plugins: {} },
      { files: ["**/*.ts"], rules: { "no-var": 2, "x/plain": [1, { o: 1 }] } },
      [{ ignores: ["dist/**"] }],
      { rules: { "x/typed": "off" } },
    ],
    a: { extends: ["shadowed"] },
    b: { extends: ["y"], rules: {} },
    p: [{ basePath: "src", files: ["lib/**"], rules: { "no-var": "error" } }],
    r: { rules: { "x/plain": ["error", { pattern: /forbidden/ }] } },
    t: { rules: { "x/typed": "error" } },
  },
};
""",
}


def test_the_program_prints_each_preset_s_rules_as_data(installed, tmp_path):
    for name, content in PACKAGES.items():
        path = tmp_path / "node_modules" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content))
    (tmp_path / "package.json").write_text("{}")
    argument = {
        "plugins": {"x": "eslint-plugin-x"},
        "presets": {
            "eslint": ["recommended", "all"],
            "eslint-plugin-x": ["a", "b", "p", "r", "t", "c"],
        },
    }
    node = load_bundled_policy().tools["eslint"].node
    assert node is not None
    with on_machine(installed):
        env = dict(os.environ, MISE_OFFLINE="0")
        folder = npm_tool.pinned_node(node.version, archives(node.platforms))
    command = [f"{folder}/node", PROGRAM, tmp_path, json.dumps(argument)]
    assert json.loads(success(command, tmp_path, env).stdout) == {
        "eslint": {
            "configs": ["recommended"],
            "presets": {
                "recommended": {
                    "blocks": [{"rules": {"no-undef": "error", "no-var": "error"}}],
                    "typed": [],
                }
            },
        },
        "eslint-plugin-x": {
            "configs": ["flat/a", "a", "b", "p", "r", "t"],
            "presets": {
                "a": {
                    "blocks": [
                        {
                            "files": ["**/*.ts"],
                            "rules": {"no-var": "error", "x/plain": ["warn", {"o": 1}]},
                        },
                        {"rules": {"x/typed": "off"}},
                    ],
                    "typed": [],
                },
                "b": {"blocks": [{"extends": ["y"], "rules": {}}], "typed": []},
                "p": {
                    "blocks": [
                        {
                            "basePath": "src",
                            "files": ["lib/**"],
                            "rules": {"no-var": "error"},
                        }
                    ],
                    "typed": [],
                },
                "r": {
                    "blocks": [{"rules": {"x/plain": ["error", None]}}],
                    "typed": [],
                },
                "t": {
                    "blocks": [{"rules": {"x/typed": "error"}}],
                    "typed": ["x/typed"],
                },
            },
        },
    }
