all: odf odfview.1

txt: odfview.txt

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
	@python3 odfview --list ../tests/examples/empty.ods
