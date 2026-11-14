all: odf odfamountview.1

txt: odfamountview.txt

%.1: %.docbook
	xmlto man $<

%.txt: %.docbook
	xmlto txt $<

clean:
	rm -f *.1 *~ *.txt odf
odf:
	ln -s ../odf

test: clean odf
	@echo ===================== output by Python3 =======================
	@python3 odfamountview ../tests/examples/empty.ods || true
