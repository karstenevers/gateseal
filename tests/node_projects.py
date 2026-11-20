"""A TypeScript project that depends on zod, for tests of tools that read it.

src/shop.ts imports zod, so tsc passes only when it reads the packages that
the project's lockfile pins. The project lists no TypeScript of its own, so
only a policy's pin provides tsc.

Each lockfile is one that its package manager wrote: npm 11.6.2 on Node
24.11.1, npm 11.19.1 on Node 24.22.3 for EACH_KIND, or pnpm 10.27.0, which the
pnpm project's packageManager field names. npm 11.19.1 wrote
NPM_EACH_KIND_WITHOUT_PEERS with npm install --package-lock-only from
NPM_EACH_KIND and an .npmrc with legacy-peer-deps=true. It wrote the lockfile
that NPM_GIT_DEPENDENCIES copies for two git dependencies in local bare
repositories, and the template takes their URLs and commits.
Its devEngines field names pnpm too, which stops npm from running in the
project.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from string import Template

ZOD = "4.1.12"
NEWER_ZOD = "4.3.6"
"""A zod release with z.mac(), which ZOD lacks."""
OWN_TYPESCRIPT = "5.9.3"
"""A TypeScript release older than any policy's, for a project that lists one."""

TSCONFIG = """\
{
  "compilerOptions": {
    "strict": true,
    "module": "nodenext",
    "target": "es2022",
    "noEmit": true,
    "skipLibCheck": true
  },
  "include": ["src"]
}
"""
REMOVED_OPTION = TSCONFIG.replace(
    '"skipLibCheck": true', '"skipLibCheck": true,\n    "baseUrl": "."'
)
"""tsconfig.json with baseUrl, which TypeScript 7 removed and OWN_TYPESCRIPT reads.

TypeScript 6 stops at baseUrl too, as a deprecated option.
"""
DEPRECATED_OPTION = REMOVED_OPTION.replace(
    '"baseUrl": "."', '"baseUrl": ".",\n    "ignoreDeprecations": "6.0"'
)
"""tsconfig.json with baseUrl, which TypeScript 6 then accepts and 7 refuses."""
SHOP = """\
import { z } from "zod";

export const Item = z.object({ name: z.string(), cents: z.number() });
export type Item = z.infer<typeof Item>;

export const label = (item: Item): string => `${item.name}: ${item.cents}`;
"""
TOTAL = """\
import type { Item } from "./shop.js";

export const total = (item: Item): number => item.cents + 1;
"""
WRONG_TOTAL = TOTAL.replace("): number", "): string")
"""An error that only zod's types show: Item's cents come from its schema."""
DEVICE = """\
import { z } from "zod";

export const Device = z.object({ address: z.mac() });
"""
"""A schema that only NEWER_ZOD's types accept."""


@dataclass(frozen=True)
class Dependencies:
    """What package.json lists: zod, maybe a TypeScript of the project's own, and
    maybe a dependency of each other kind."""

    zod: str = ZOD
    typescript: str | None = None
    each_kind: bool = False


PINNED = Dependencies()
NEWER = Dependencies(zod=NEWER_ZOD)
OWN_COMPILER = Dependencies(typescript=OWN_TYPESCRIPT)
EACH_KIND = Dependencies(zod=f"^{ZOD}", each_kind=True)
"""zod by a range that NEWER_ZOD meets, and a dependency of each other kind.

A package manager that ignores the lockfile installs the newest zod in the range.
"""
DEVELOPMENT = {"is-number": "7.0.0"}
OPTIONAL = {"is-odd": "3.0.1"}
PEER = {"@types/react": "19.3.0"}
"""The peer dependency of @types/react-dom, at the version that the lockfiles pin."""


@dataclass(frozen=True)
class Manager:
    """A package manager, its fields of package.json, and the lockfiles it wrote."""

    name: str
    lockfile: str
    fields: Mapping[str, object]
    locks: Mapping[Dependencies, str]

    def package_json(self, dependencies: Dependencies) -> str:
        runtime = {"zod": dependencies.zod}
        if dependencies.each_kind:
            runtime["@types/react-dom"] = "19.2.3"
        package: dict[str, object] = {
            "name": "shop",
            "version": "1.0.0",
            "private": True,
            "type": "module",
            "dependencies": runtime,
        }
        if dependencies.typescript is not None:
            package["devDependencies"] = {"typescript": dependencies.typescript}
        if dependencies.each_kind:
            package["devDependencies"] = DEVELOPMENT
            package["optionalDependencies"] = OPTIONAL
        return json.dumps(package | dict(self.fields), indent=2) + "\n"


NPM_PINNED = """\
{
  "name": "shop",
  "version": "1.0.0",
  "lockfileVersion": 3,
  "requires": true,
  "packages": {
    "": {
      "name": "shop",
      "version": "1.0.0",
      "dependencies": {
        "zod": "4.1.12"
      }
    },
    "node_modules/zod": {
      "version": "4.1.12",
      "resolved": "https://registry.npmjs.org/zod/-/zod-4.1.12.tgz",
      "integrity": "sha512-JInaHOamG8pt5+Ey8kGmdcAcg3OL9reK8ltczgHTAwNhMys/6ThXHityHxVV2p3fkw/c+MAvBHFVYHFZDmjMCQ==",
      "license": "MIT",
      "funding": {
        "url": "https://github.com/sponsors/colinhacks"
      }
    }
  }
}
"""  # noqa: E501

NPM_NEWER = """\
{
  "name": "shop",
  "version": "1.0.0",
  "lockfileVersion": 3,
  "requires": true,
  "packages": {
    "": {
      "name": "shop",
      "version": "1.0.0",
      "dependencies": {
        "zod": "4.3.6"
      }
    },
    "node_modules/zod": {
      "version": "4.3.6",
      "resolved": "https://registry.npmjs.org/zod/-/zod-4.3.6.tgz",
      "integrity": "sha512-rftlrkhHZOcjDwkGlnUtZZkvaPHCsDATp4pGpuOOMDaTdDDXF91wuVDJoWoPsKX/3YPQ5fHuF3STjcYyKr+Qhg==",
      "license": "MIT",
      "funding": {
        "url": "https://github.com/sponsors/colinhacks"
      }
    }
  }
}
"""  # noqa: E501

NPM_OWN_COMPILER = """\
{
  "name": "shop",
  "version": "1.0.0",
  "lockfileVersion": 3,
  "requires": true,
  "packages": {
    "": {
      "name": "shop",
      "version": "1.0.0",
      "dependencies": {
        "zod": "4.1.12"
      },
      "devDependencies": {
        "typescript": "5.9.3"
      }
    },
    "node_modules/typescript": {
      "version": "5.9.3",
      "resolved": "https://registry.npmjs.org/typescript/-/typescript-5.9.3.tgz",
      "integrity": "sha512-jl1vZzPDinLr9eUt3J/t7V6FgNEw9QjvBPdysz9KfQDD41fQrC2Y4vKQdiaUpFT4bXlb1RHhLpp8wtm6M5TgSw==",
      "dev": true,
      "license": "Apache-2.0",
      "bin": {
        "tsc": "bin/tsc",
        "tsserver": "bin/tsserver"
      },
      "engines": {
        "node": ">=14.17"
      }
    },
    "node_modules/zod": {
      "version": "4.1.12",
      "resolved": "https://registry.npmjs.org/zod/-/zod-4.1.12.tgz",
      "integrity": "sha512-JInaHOamG8pt5+Ey8kGmdcAcg3OL9reK8ltczgHTAwNhMys/6ThXHityHxVV2p3fkw/c+MAvBHFVYHFZDmjMCQ==",
      "license": "MIT",
      "funding": {
        "url": "https://github.com/sponsors/colinhacks"
      }
    }
  }
}
"""  # noqa: E501

PNPM_PINNED = """\
lockfileVersion: '9.0'

settings:
  autoInstallPeers: true
  excludeLinksFromLockfile: false

importers:

  .:
    dependencies:
      zod:
        specifier: 4.1.12
        version: 4.1.12

packages:

  zod@4.1.12:
    resolution: {integrity: sha512-JInaHOamG8pt5+Ey8kGmdcAcg3OL9reK8ltczgHTAwNhMys/6ThXHityHxVV2p3fkw/c+MAvBHFVYHFZDmjMCQ==}

snapshots:

  zod@4.1.12: {}
"""  # noqa: E501

PNPM_NEWER = """\
lockfileVersion: '9.0'

settings:
  autoInstallPeers: true
  excludeLinksFromLockfile: false

importers:

  .:
    dependencies:
      zod:
        specifier: 4.3.6
        version: 4.3.6

packages:

  zod@4.3.6:
    resolution: {integrity: sha512-rftlrkhHZOcjDwkGlnUtZZkvaPHCsDATp4pGpuOOMDaTdDDXF91wuVDJoWoPsKX/3YPQ5fHuF3STjcYyKr+Qhg==}

snapshots:

  zod@4.3.6: {}
"""  # noqa: E501

PNPM_OWN_COMPILER = """\
lockfileVersion: '9.0'

settings:
  autoInstallPeers: true
  excludeLinksFromLockfile: false

importers:

  .:
    dependencies:
      zod:
        specifier: 4.1.12
        version: 4.1.12
    devDependencies:
      typescript:
        specifier: 5.9.3
        version: 5.9.3

packages:

  typescript@5.9.3:
    resolution: {integrity: sha512-jl1vZzPDinLr9eUt3J/t7V6FgNEw9QjvBPdysz9KfQDD41fQrC2Y4vKQdiaUpFT4bXlb1RHhLpp8wtm6M5TgSw==}
    engines: {node: '>=14.17'}
    hasBin: true

  zod@4.1.12:
    resolution: {integrity: sha512-JInaHOamG8pt5+Ey8kGmdcAcg3OL9reK8ltczgHTAwNhMys/6ThXHityHxVV2p3fkw/c+MAvBHFVYHFZDmjMCQ==}

snapshots:

  typescript@5.9.3: {}

  zod@4.1.12: {}
"""  # noqa: E501


NPM_EACH_KIND = """\
{
  "name": "shop",
  "version": "1.0.0",
  "lockfileVersion": 3,
  "requires": true,
  "packages": {
    "": {
      "name": "shop",
      "version": "1.0.0",
      "dependencies": {
        "@types/react-dom": "19.2.3",
        "zod": "^4.1.12"
      },
      "devDependencies": {
        "is-number": "7.0.0"
      },
      "optionalDependencies": {
        "is-odd": "3.0.1"
      }
    },
    "node_modules/@types/react": {
      "version": "19.3.0",
      "resolved": "https://registry.npmjs.org/@types/react/-/react-19.3.0.tgz",
      "integrity": "sha512-N0rFCuH9YoxG9/m61l9MfpJKfmLOVU0em7ipIz6TRgSSkvReLB9vL85GB+yr8Bs5leqpvg96JSwF4ZS1s4viQg==",
      "license": "MIT",
      "peer": true,
      "dependencies": {
        "csstype": "^3.2.2"
      }
    },
    "node_modules/@types/react-dom": {
      "version": "19.2.3",
      "resolved": "https://registry.npmjs.org/@types/react-dom/-/react-dom-19.2.3.tgz",
      "integrity": "sha512-jp2L/eY6fn+KgVVQAOqYItbF0VY/YApe5Mz2F0aykSO8gx31bYCZyvSeYxCHKvzHG5eZjc+zyaS5BrBWya2+kQ==",
      "license": "MIT",
      "peerDependencies": {
        "@types/react": "^19.2.0"
      }
    },
    "node_modules/csstype": {
      "version": "3.2.4",
      "resolved": "https://registry.npmjs.org/csstype/-/csstype-3.2.4.tgz",
      "integrity": "sha512-PoKaoPzQapnEoqUSnHx1u6W6ku8CVEgKsnpmmzU3m1nwkidpPBQ8V/Zq2+bWNrsOKoKLwTGdbx6xF7BXP1zLVQ==",
      "license": "MIT",
      "peer": true
    },
    "node_modules/is-number": {
      "version": "7.0.0",
      "resolved": "https://registry.npmjs.org/is-number/-/is-number-7.0.0.tgz",
      "integrity": "sha512-41Cifkg6e8TylSpdtTpeLVMqvSBEVzTttHvERD741+pnZ8ANv0004MRL43QKPDlK9cGvNp6NZWZUBlbGXYxxng==",
      "dev": true,
      "license": "MIT",
      "engines": {
        "node": ">=0.12.0"
      }
    },
    "node_modules/is-odd": {
      "version": "3.0.1",
      "resolved": "https://registry.npmjs.org/is-odd/-/is-odd-3.0.1.tgz",
      "integrity": "sha512-CQpnWPrDwmP1+SMHXZhtLtJv90yiyVfluGsX5iNCVkrhQtU3TQHsUWPG9wkdk9Lgd5yNpAg9jQEo90CBaXgWMA==",
      "license": "MIT",
      "optional": true,
      "dependencies": {
        "is-number": "^6.0.0"
      },
      "engines": {
        "node": ">=4"
      }
    },
    "node_modules/is-odd/node_modules/is-number": {
      "version": "6.0.0",
      "resolved": "https://registry.npmjs.org/is-number/-/is-number-6.0.0.tgz",
      "integrity": "sha512-Wu1VHeILBK8KAWJUAiSZQX94GmOE45Rg6/538fKwiloUu21KncEkYGPqob2oSZ5mUT73vLGrHQjKw3KMPwfDzg==",
      "license": "MIT",
      "optional": true,
      "engines": {
        "node": ">=0.10.0"
      }
    },
    "node_modules/zod": {
      "version": "4.1.12",
      "resolved": "https://registry.npmjs.org/zod/-/zod-4.1.12.tgz",
      "integrity": "sha512-JInaHOamG8pt5+Ey8kGmdcAcg3OL9reK8ltczgHTAwNhMys/6ThXHityHxVV2p3fkw/c+MAvBHFVYHFZDmjMCQ==",
      "license": "MIT",
      "funding": {
        "url": "https://github.com/sponsors/colinhacks"
      }
    }
  }
}
"""  # noqa: E501

PNPM_EACH_KIND = """\
lockfileVersion: '9.0'

settings:
  autoInstallPeers: true
  excludeLinksFromLockfile: false

importers:

  .:
    dependencies:
      '@types/react-dom':
        specifier: 19.2.3
        version: 19.2.3(@types/react@19.3.0)
      zod:
        specifier: ^4.1.12
        version: 4.1.12
    devDependencies:
      is-number:
        specifier: 7.0.0
        version: 7.0.0
    optionalDependencies:
      is-odd:
        specifier: 3.0.1
        version: 3.0.1

packages:

  '@types/react-dom@19.2.3':
    resolution: {integrity: sha512-jp2L/eY6fn+KgVVQAOqYItbF0VY/YApe5Mz2F0aykSO8gx31bYCZyvSeYxCHKvzHG5eZjc+zyaS5BrBWya2+kQ==}
    peerDependencies:
      '@types/react': ^19.2.0

  '@types/react@19.3.0':
    resolution: {integrity: sha512-N0rFCuH9YoxG9/m61l9MfpJKfmLOVU0em7ipIz6TRgSSkvReLB9vL85GB+yr8Bs5leqpvg96JSwF4ZS1s4viQg==}

  csstype@3.2.4:
    resolution: {integrity: sha512-PoKaoPzQapnEoqUSnHx1u6W6ku8CVEgKsnpmmzU3m1nwkidpPBQ8V/Zq2+bWNrsOKoKLwTGdbx6xF7BXP1zLVQ==}

  is-number@6.0.0:
    resolution: {integrity: sha512-Wu1VHeILBK8KAWJUAiSZQX94GmOE45Rg6/538fKwiloUu21KncEkYGPqob2oSZ5mUT73vLGrHQjKw3KMPwfDzg==}
    engines: {node: '>=0.10.0'}

  is-number@7.0.0:
    resolution: {integrity: sha512-41Cifkg6e8TylSpdtTpeLVMqvSBEVzTttHvERD741+pnZ8ANv0004MRL43QKPDlK9cGvNp6NZWZUBlbGXYxxng==}
    engines: {node: '>=0.12.0'}

  is-odd@3.0.1:
    resolution: {integrity: sha512-CQpnWPrDwmP1+SMHXZhtLtJv90yiyVfluGsX5iNCVkrhQtU3TQHsUWPG9wkdk9Lgd5yNpAg9jQEo90CBaXgWMA==}
    engines: {node: '>=4'}

  zod@4.1.12:
    resolution: {integrity: sha512-JInaHOamG8pt5+Ey8kGmdcAcg3OL9reK8ltczgHTAwNhMys/6ThXHityHxVV2p3fkw/c+MAvBHFVYHFZDmjMCQ==}

snapshots:

  '@types/react-dom@19.2.3(@types/react@19.3.0)':
    dependencies:
      '@types/react': 19.3.0

  '@types/react@19.3.0':
    dependencies:
      csstype: 3.2.4

  csstype@3.2.4: {}

  is-number@6.0.0:
    optional: true

  is-number@7.0.0: {}

  is-odd@3.0.1:
    dependencies:
      is-number: 6.0.0
    optional: true

  zod@4.1.12: {}
"""  # noqa: E501


NPM_EACH_KIND_WITHOUT_PEERS = """\
{
  "name": "shop",
  "version": "1.0.0",
  "lockfileVersion": 3,
  "requires": true,
  "packages": {
    "": {
      "name": "shop",
      "version": "1.0.0",
      "dependencies": {
        "@types/react-dom": "19.2.3",
        "zod": "^4.1.12"
      },
      "devDependencies": {
        "is-number": "7.0.0"
      },
      "optionalDependencies": {
        "is-odd": "3.0.1"
      }
    },
    "node_modules/@types/react-dom": {
      "version": "19.2.3",
      "resolved": "https://registry.npmjs.org/@types/react-dom/-/react-dom-19.2.3.tgz",
      "integrity": "sha512-jp2L/eY6fn+KgVVQAOqYItbF0VY/YApe5Mz2F0aykSO8gx31bYCZyvSeYxCHKvzHG5eZjc+zyaS5BrBWya2+kQ==",
      "license": "MIT",
      "peerDependencies": {
        "@types/react": "^19.2.0"
      }
    },
    "node_modules/is-number": {
      "version": "7.0.0",
      "resolved": "https://registry.npmjs.org/is-number/-/is-number-7.0.0.tgz",
      "integrity": "sha512-41Cifkg6e8TylSpdtTpeLVMqvSBEVzTttHvERD741+pnZ8ANv0004MRL43QKPDlK9cGvNp6NZWZUBlbGXYxxng==",
      "dev": true,
      "license": "MIT",
      "engines": {
        "node": ">=0.12.0"
      }
    },
    "node_modules/is-odd": {
      "version": "3.0.1",
      "resolved": "https://registry.npmjs.org/is-odd/-/is-odd-3.0.1.tgz",
      "integrity": "sha512-CQpnWPrDwmP1+SMHXZhtLtJv90yiyVfluGsX5iNCVkrhQtU3TQHsUWPG9wkdk9Lgd5yNpAg9jQEo90CBaXgWMA==",
      "license": "MIT",
      "optional": true,
      "dependencies": {
        "is-number": "^6.0.0"
      },
      "engines": {
        "node": ">=4"
      }
    },
    "node_modules/is-odd/node_modules/is-number": {
      "version": "6.0.0",
      "resolved": "https://registry.npmjs.org/is-number/-/is-number-6.0.0.tgz",
      "integrity": "sha512-Wu1VHeILBK8KAWJUAiSZQX94GmOE45Rg6/538fKwiloUu21KncEkYGPqob2oSZ5mUT73vLGrHQjKw3KMPwfDzg==",
      "license": "MIT",
      "optional": true,
      "engines": {
        "node": ">=0.10.0"
      }
    },
    "node_modules/zod": {
      "version": "4.1.12",
      "resolved": "https://registry.npmjs.org/zod/-/zod-4.1.12.tgz",
      "integrity": "sha512-JInaHOamG8pt5+Ey8kGmdcAcg3OL9reK8ltczgHTAwNhMys/6ThXHityHxVV2p3fkw/c+MAvBHFVYHFZDmjMCQ==",
      "license": "MIT",
      "funding": {
        "url": "https://github.com/sponsors/colinhacks"
      }
    }
  }
}
"""  # noqa: E501
"""NPM_EACH_KIND without the peer dependency and its dependency."""

NPM_GIT_DEPENDENCIES = Template("""\
{
  "name": "shop",
  "version": "1.0.0",
  "lockfileVersion": 3,
  "requires": true,
  "packages": {
    "": {
      "name": "shop",
      "version": "1.0.0",
      "dependencies": {
        "outer": "git+file://$outer",
        "zod": "4.1.12"
      }
    },
    "node_modules/inner": {
      "version": "1.0.0",
      "resolved": "git+file://$inner#$inner_commit"
    },
    "node_modules/outer": {
      "version": "1.0.0",
      "resolved": "git+file://$outer#$outer_commit",
      "dependencies": {
        "inner": "git+file://$inner"
      }
    },
    "node_modules/zod": {
      "version": "4.1.12",
      "resolved": "https://registry.npmjs.org/zod/-/zod-4.1.12.tgz",
      "integrity": "sha512-JInaHOamG8pt5+Ey8kGmdcAcg3OL9reK8ltczgHTAwNhMys/6ThXHityHxVV2p3fkw/c+MAvBHFVYHFZDmjMCQ==",
      "license": "MIT",
      "funding": {
        "url": "https://github.com/sponsors/colinhacks"
      }
    }
  }
}
""")  # noqa: E501
"""The lockfile of zod and the git dependency outer, which depends on the git
dependency inner. Each takes its bare repository's path and the commit."""


NPM = Manager(
    "npm",
    "package-lock.json",
    {},
    {
        PINNED: NPM_PINNED,
        NEWER: NPM_NEWER,
        OWN_COMPILER: NPM_OWN_COMPILER,
        EACH_KIND: NPM_EACH_KIND,
    },
)
PNPM = Manager(
    "pnpm",
    "pnpm-lock.yaml",
    {
        "packageManager": "pnpm@10.27.0",
        "devEngines": {"packageManager": {"name": "pnpm"}},
    },
    {
        PINNED: PNPM_PINNED,
        NEWER: PNPM_NEWER,
        OWN_COMPILER: PNPM_OWN_COMPILER,
        EACH_KIND: PNPM_EACH_KIND,
    },
)
MANAGERS = (NPM, PNPM)


def write_package(root, manager, dependencies=PINNED, lockfile=True):
    """Write package.json for dependencies, and manager's lockfile for them."""
    (root / "package.json").write_text(manager.package_json(dependencies))
    if lockfile:
        (root / manager.lockfile).write_text(manager.locks[dependencies])


def write_project(root, manager):
    """Write the project to root, with manager's lockfile and no node_modules."""
    files = {
        ".gitignore": "node_modules/\n",
        "tsconfig.json": TSCONFIG,
        "src/shop.ts": SHOP,
    }
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    write_package(root, manager)
    return root
