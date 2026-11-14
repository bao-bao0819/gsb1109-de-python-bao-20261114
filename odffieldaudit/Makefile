all: odf odffieldaudit.1

txt: odffieldaudit.txt

%.1: %.docbook
	xmlto man $<

%.txt: %.docbook
	xmlto txt $<

clean:
	rm -f *.1 *~ *.txt odf

odf:
	ln -s ../odf
