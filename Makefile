.PHONY: check test

check:
	python3 -m compileall -q src tests
	PYTHONPATH=src python3 -m encodec_live_streamer --help >/dev/null

test: check
	PYTHONPATH=src python3 -m unittest discover -s tests -v

