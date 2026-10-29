"""State wholesaling regulations (tumarketguide.com): parsing + lookups."""

import unittest

from crm.tests.frappe_shim import install

install()

from crm.api import state_regulations as sr  # noqa: E402

BUNDLE = (
	"var Qd=m({method:`GET`}).handler(u(`" + "a" * 64 + "`)),"
	"$d=m({method:`GET`}).handler(u(`" + "b" * 64 + "`)),"
	"Rp=m({method:`GET`}).handler(u(`" + "c" * 64 + "`)),"
	"ef=dd({queryKey:[`states`],queryFn:()=>Qd()}),"
	"sm=e=>dd({queryKey:[`state`,e],queryFn:()=>$d({data:{slug:e}})})"
)

DETAIL = {"t": 10, "i": 0, "p": {"k": ["result", "error"], "v": [
	{"t": 10, "i": 1, "p": {"k": ["state", "companies", "documents"], "v": [
		{"t": 10, "i": 2, "p": {"k": ["name", "slug", "kind", "regulations", "severity", "general_notes", "show"], "v": [
			{"t": 1, "s": "Oklahoma"}, {"t": 1, "s": "oklahoma"}, {"t": 1, "s": "attorney"},
			{"t": 1, "s": "Use the state contract\\n\\nSource: x"}, {"t": 1, "s": "High"},
			{"t": 1, "s": ""}, {"t": 2, "s": 2},
		]}},
		{"t": 9, "i": 3, "a": []},
		{"t": 9, "i": 4, "a": [{"t": 10, "i": 5, "p": {"k": ["title", "url"], "v": [
			{"t": 1, "s": "OK Contract"}, {"t": 1, "s": "https://docs.google.com/x"},
		]}}]},
	]}},
	{"t": 2, "s": 1},
]}}


class StateRegulationTests(unittest.TestCase):
	def test_state_code(self):
		self.assertEqual(sr.state_code("in"), "IN")
		self.assertEqual(sr.state_code(" North Carolina "), "NC")
		self.assertEqual(sr.state_code("Indy"), "")
		self.assertEqual(sr.state_code(None), "")

	def test_find_server_fn_ids(self):
		self.assertEqual(sr.find_server_fn_ids(BUNDLE), ("a" * 64, "b" * 64))
		with self.assertRaises(ValueError):
			sr.find_server_fn_ids("nothing here")

	def test_decode_and_trim(self):
		out = sr.decode_seroval(DETAIL)
		self.assertIsNone(out["error"])
		s = sr.trim_state(out["result"])
		self.assertEqual(s["code"], "OK")
		self.assertEqual(s["severity"], "High")
		self.assertEqual(s["regulations"], "Use the state contract\n\nSource: x")
		self.assertEqual(s["documents"], [{"title": "OK Contract", "url": "https://docs.google.com/x"}])
		self.assertEqual(s["url"], "https://tumarketguide.com/states/oklahoma")

	def test_regulation_for_state_only_flags_states_with_rules(self):
		guide = {"states": {
			"OK": {"name": "Oklahoma", "severity": "High", "regulations": "x", "documents": []},
			"MO": {"name": "Missouri", "severity": "UK", "regulations": "", "documents": [{"title": "f", "url": "u"}]},
			"GA": {"name": "Georgia", "severity": "", "regulations": "", "documents": []},
		}}
		self.assertEqual(sr.regulation_for_state("ok", guide)["severity_label"], "High enforcement")
		self.assertEqual(sr.regulation_for_state("Missouri", guide)["severity_label"], "Unknown enforcement")
		self.assertIsNone(sr.regulation_for_state("GA", guide))
		self.assertIsNone(sr.regulation_for_state("", guide))

	def test_changed_states(self):
		a = {"states": {"OK": {"severity": "High", "regulations": "x", "documents": []}}}
		b = {"states": {
			"OK": {"severity": "Severe", "regulations": "x", "documents": []},
			"TX": {"severity": "Low", "regulations": "y", "documents": []},
		}}
		self.assertEqual(sr.changed_states(a, b), ["OK", "TX"])
		self.assertEqual(sr.changed_states(a, a), [])

	def test_snapshot_loads(self):
		snap = sr._load_snapshot()
		self.assertGreaterEqual(len(snap["states"]), 40)
		self.assertTrue(sr.has_rules(snap["states"]["OK"]))


if __name__ == "__main__":
	unittest.main()
