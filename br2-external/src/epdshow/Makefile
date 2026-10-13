CFLAGS ?= -O2
CFLAGS += -Wall -Wextra

epdshow: epdshow.c
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $<

install: epdshow
	install -D -m 0755 epdshow $(DESTDIR)/usr/bin/epdshow

clean:
	rm -f epdshow
