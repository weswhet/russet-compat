#!/usr/bin/env python3
"""Freeze promoted processor manifests without importing Python processor code."""
import argparse
import ast
import hashlib
import json

from compat_paths import COMMUNITY_SOURCE as SOURCE, COMPATIBILITY

TARGET = COMPATIBILITY / 'community-processors.json'
REFERENCE = '0f7b61ab061c77710be4140c404910a11f89fc7b'
ALIASES = {'com.github.autopkg.AutoPkgGitMaster/GenerateRelocatablePython': 'GenerateRelocatablePython'}


def literal(node, names):
    if isinstance(node, ast.Name):
        return names[node.id]
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        values = [literal(v, names) for v in node.elts]
        return tuple(values) if isinstance(node, ast.Tuple) else values
    if isinstance(node, ast.Dict):
        return {literal(k, names): literal(v, names) for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.JoinedStr):
        return ''.join(str(literal(v.value, names)) if isinstance(v, ast.FormattedValue) else v.value for v in node.values)
    if isinstance(node, ast.BinOp):
        left, right = literal(node.left, names), literal(node.right, names)
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.Add):
            return left + right
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'join' and len(node.args) == 1:
        return literal(node.func.value, names).join(literal(node.args[0], names))
    raise ValueError('Unsupported manifest expression: ' + ast.dump(node))


def ordering(value):
    if isinstance(value, dict):
        return [[key, ordering(item)] for key, item in value.items()]
    return {'display': str(value)}


def capture():
    processors, order, sources = {}, {}, {}
    for path in sorted(SOURCE.glob('*/*.py')):
        tree = ast.parse(path.read_bytes(), filename=str(path))
        names = {}
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and node.value is not None:
                        try:
                            names[target.id] = literal(node.value, names)
                        except (ValueError, KeyError, TypeError):
                            pass
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == path.stem)
        names['__doc__'] = ast.get_docstring(cls, clean=False)
        values = {'description': None, 'input_variables': {}, 'output_variables': {}, 'lifecycle': {}}
        for node in cls.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in values:
                        values[target.id] = literal(node.value, names)
        processors[cls.name] = values
        order[cls.name] = {key: ordering(values[key]) for key in ('description', 'input_variables', 'output_variables')}
        sources[cls.name] = {'path': path.relative_to(SOURCE).as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    if len(processors) != 12:
        raise ValueError('Expected twelve promoted implementations')
    return {'schema_version': 1, 'repository': 'https://github.com/autopkg/recipes',
            'reference_commit': REFERENCE, 'processors': processors, 'ordering': order,
            'aliases': ALIASES, 'sources': sources}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    encoded = json.dumps(capture(), indent=2, ensure_ascii=False) + '\n'
    if args.check:
        if TARGET.read_text() != encoded:
            raise SystemExit('Promoted processor reference differs; inspect and recapture intentionally')
        print('Promoted processor contract matches all 12 pinned source files and the shared alias.')
    else:
        TARGET.write_text(encoded)


if __name__ == '__main__':
    main()
