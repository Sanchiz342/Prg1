PY ?= .venv/bin/python

.PHONY: setup run run-offline full-refresh test dashboard clean

setup:            ## create the virtualenv with pinned dependencies
	python3 -m venv .venv && $(PY) -m pip install -r requirements-dev.txt

run:              ## download (if changed) -> load -> dbt build -> freshness -> export
	$(PY) -m pipeline run

run-offline:      ## same, from the last downloaded files
	$(PY) -m pipeline run --offline

full-refresh:     ## rebuild the incremental fact table from scratch
	$(PY) -m pipeline run --full-refresh

test:             ## unit + dbt tests on hand-made fixtures (no network)
	$(PY) -m pytest

dashboard:        ## serve the static dashboard on http://localhost:8000
	cd dashboard && python3 -m http.server 8000

clean:
	rm -rf data dbt/target dbt/logs .pytest_cache
