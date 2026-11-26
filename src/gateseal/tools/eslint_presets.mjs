// gateseal policy lock runs this program once on the policy's Node. It reads
// the presets that an ESLint rule file names from the packages that npm
// installed in FOLDER, and prints them as data.
//
//   node eslint_presets.mjs FOLDER ARGUMENT
//
// ARGUMENT is {"plugins": {prefix: package}, "presets": {package: [name]}}.
// The output maps each package to {"configs": [key], "presets": {name: preset}},
// where configs lists the keys of the package's configs, and presets holds
// each name that the package has, under flat/<name> or <name>. A preset is
// {"blocks": [block], "typed": [rule]}. A block is one object of the preset
// that sets more than files and ignores, without its settings, such as
// languageOptions; each severity is off, warn or error. A key that changes
// where the rules apply, such as basePath, stays, so that lock refuses it.
// Options that JSON would change, such as a regular expression, print as
// null, which lock refuses. typed lists each rule that the preset turns on
// and whose plugin says it needs type information.
// eslint/recommended is the built-in rules that ESLint marks recommended, as
// @eslint/js lists them, read from the installed ESLint itself.
import { createRequire } from "node:module";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { isDeepStrictEqual } from "node:util";

const [folder, argument] = process.argv.slice(2);
const { plugins, presets } = JSON.parse(argument);
const tool = createRequire(join(folder, "package.json"));
const load = async (name) => {
  const module = await import(pathToFileURL(tool.resolve(name)).href);
  return module.default ?? module;
};
const SEVERITIES = ["off", "warn", "error"];
const severity = (value) => {
  const first = [value].flat()[0];
  return SEVERITIES[first] ?? first;
};
const jsonOptions = (values) =>
  isDeepStrictEqual(values, JSON.parse(JSON.stringify(values))) ? values : [null];
const SETTINGS = new Set([
  "name",
  "language",
  "languageOptions",
  "linterOptions",
  "plugins",
  "processor",
  "settings",
]);
const { builtinRules } = await load("eslint/use-at-your-own-risk");
const rules = new Map(builtinRules);
for (const [prefix, name] of Object.entries(plugins)) {
  for (const [rule, value] of Object.entries((await load(name)).rules ?? {})) {
    rules.set(`${prefix}/${rule}`, value);
  }
}
const recommended = Object.fromEntries(
  [...builtinRules]
    .filter(([, rule]) => rule.meta?.docs?.recommended)
    .map(([name]) => [name, "error"]),
);
const configs = async (name) =>
  name === "eslint"
    ? { recommended: { rules: recommended } }
    : ((await load(name)).configs ?? {});
const block = (object) => {
  const kept = Object.fromEntries(
    Object.entries(object).filter(([key]) => !SETTINGS.has(key)),
  );
  if (kept.rules) {
    kept.rules = Object.fromEntries(
      Object.entries(kept.rules).map(([name, value]) => [
        name,
        Array.isArray(value) ? [severity(value), ...jsonOptions(value.slice(1))] : severity(value),
      ]),
    );
  }
  return kept;
};
const output = {};
for (const [name, wanted] of Object.entries(presets)) {
  const found = await configs(name);
  output[name] = { configs: Object.keys(found), presets: {} };
  for (const preset of wanted) {
    const key = [`flat/${preset}`, preset].find((key) => Object.hasOwn(found, key));
    if (key === undefined) {
      continue;
    }
    // A block of only files and ignores sets no rule: a global ignore, or the
    // files that a parser of the preset reads.
    const blocks = [found[key]]
      .flat(Infinity)
      .map(block)
      .filter((kept) => Object.keys(kept).some((key) => key !== "files" && key !== "ignores"));
    const typed = blocks
      .flatMap((kept) => Object.entries(kept.rules ?? {}))
      .filter(
        ([rule, value]) =>
          severity(value) !== "off" && rules.get(rule)?.meta?.docs?.requiresTypeChecking,
      )
      .map(([rule]) => rule);
    output[name].presets[preset] = { blocks, typed: [...new Set(typed)] };
  }
}
process.stdout.write(JSON.stringify(output));
