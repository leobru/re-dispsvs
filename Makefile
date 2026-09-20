.NOTPARALLEL:
.PHONY: all build verify test clean
all:
	python3 svs.py all
build:
	python3 svs.py build
verify:
	python3 svs.py verify
test:
	python3 -m unittest -v test_svs
clean:
	rm -rf build __pycache__
