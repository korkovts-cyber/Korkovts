from pathlib import Path
p = Path('v11239_deep_reachability.py')
text = p.read_text(encoding='utf-8')
assert 'VERSION = "11.23.9"' in text
assert 'async def screen_v11239' in text
assert 'futures.quick_deep_screen = screen_v11239' in text
assert 'ranking_fallback_safe' in text
print('V11.23.9 PREFLIGHT: OK')
