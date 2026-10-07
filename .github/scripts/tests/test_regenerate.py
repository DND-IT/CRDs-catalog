import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import crd_policy as p
import crd_regenerate as r

SHA = 'a' * 40
CRD = b'''apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
spec:
  group: example.io
  names: {kind: Widget}
  versions:
    - name: v1alpha1
      schema:
        openAPIV3Schema:
          type: object
          properties:
            spec:
              type: object
              properties:
                size: {type: integer}
    - name: v1
      schema:
        openAPIV3Schema:
          type: object
          properties:
            spec:
              type: object
              properties:
                size: {type: integer}
'''


class RegenerateTests(unittest.TestCase):
    def test_convert_writes_one_strict_schema_per_version(self):
        with tempfile.TemporaryDirectory() as workdir:
            results = r.convert(CRD, workdir)
        self.assertEqual([(g, f) for g, f, _ in results], [('example.io', 'widget_v1.json'), ('example.io', 'widget_v1alpha1.json')])
        schema = json.loads(results[0][2])
        self.assertFalse(schema['properties']['spec']['additionalProperties'])
        self.assertNotIn('additionalProperties', schema)

    def test_convert_rejects_yaml_aliases(self):
        with tempfile.TemporaryDirectory() as workdir, self.assertRaises(p.PolicyError):
            r.convert(b'a: &x {}\nb: *x\n', workdir)

    def test_regenerate_writes_schemas_and_pinned_sources(self):
        def fake_get(url, raw=False):
            if url.endswith('/git/ref/tags/v1.2.0'):
                return {'object': {'type': 'tag', 'sha': 'b' * 40}}
            if url.endswith('/git/tags/' + 'b' * 40):
                return {'object': {'type': 'commit', 'sha': SHA}}
            if url.endswith('/contents/crds?ref=' + SHA):
                return [{'type': 'file', 'name': 'widgets.yaml', 'path': 'crds/widgets.yaml'},
                        {'type': 'file', 'name': 'NOTES.txt', 'path': 'crds/NOTES.txt'}]
            self.assertEqual(url, f'https://raw.githubusercontent.com/example/operator/{SHA}/crds/widgets.yaml')
            return CRD

        sources = [{'repo': 'example/operator', 'version': 'v1.2.0', 'paths': ['crds/']}]
        with tempfile.TemporaryDirectory() as root, patch.object(r, 'get', side_effect=fake_get):
            rows = r.regenerate(sources, root)
            self.assertTrue(pathlib.Path(root, 'example.io', 'widget_v1.json').is_file())
        url = f'https://github.com/example/operator/blob/{SHA}/crds/widgets.yaml'
        self.assertEqual(rows, [('example.io/widget_v1.json', url), ('example.io/widget_v1alpha1.json', url)])
        mapping = p.parse_sources(r.render_table(rows), [schema for schema, _ in rows])
        self.assertEqual(mapping['example.io/widget_v1.json'], ('example/operator', SHA, 'crds/widgets.yaml'))


if __name__ == '__main__':
    unittest.main()
