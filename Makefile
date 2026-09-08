PYTHON ?= python3

.PHONY: check release-check build release-acceptance clean

check:
	PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v
	$(PYTHON) scripts/install.py verify-source

release-check: check
	$(PYTHON) scripts/privacy_scan.py --root .
	$(PYTHON) scripts/build_release.py --check-only

build: release-check
	$(PYTHON) scripts/build_release.py

release-acceptance: build
	$(PYTHON) scripts/validate_release.py
	$(PYTHON) scripts/release_acceptance.py dist/aag-external-storage-safe-suspend-linux-v1.1.0.run

clean:
	rm -rf build dist release-work
	find . -type d -name __pycache__ -prune -exec rm -rf '{}' +
