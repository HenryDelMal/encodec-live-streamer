.PHONY: check native test verify

check:
	python3 -m compileall -q src tests
	PYTHONPATH=src python3 -m encodec_live_streamer --help >/dev/null

test: check
	PYTHONPATH=src python3 -m unittest discover -s tests -v

native:
	cmake -S native -B build/native -DCMAKE_BUILD_TYPE=Release
	cmake --build build/native --parallel

verify: test native
	./scripts/verify-repository.sh
