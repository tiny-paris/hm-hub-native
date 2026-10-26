CC := cc
CLANG := clang
PYTHON ?= python3
CFLAGS := -O2 -Wall -Wextra -Werror
CPPFLAGS := -Ifirmware
.PHONY: all test prepare firmware check
all: build/receiver_sim.so build/stream_receiver.arm.o build/cdc_stream_rx.arm.o build/test_transport
build:
	mkdir -p build/test-results
build/test-results: | build
	mkdir -p $@
build/receiver_sim.so: firmware/stream_receiver.c firmware/stream_receiver.h firmware/cdc_stream_rx.c firmware/cdc_stream_rx.h firmware/hm_copy.h tests/sim_adapter.c | build
	$(CC) $(CPPFLAGS) $(CFLAGS) -fPIC -shared firmware/stream_receiver.c firmware/cdc_stream_rx.c tests/sim_adapter.c -o $@
build/stream_receiver.arm.o: firmware/stream_receiver.c firmware/stream_receiver.h firmware/hm_copy.h | build
	$(CLANG) --target=arm-none-eabi -mcpu=cortex-m4 -mthumb -ffreestanding -fno-builtin $(CPPFLAGS) $(CFLAGS) -c firmware/stream_receiver.c -o $@
build/cdc_stream_rx.arm.o: firmware/cdc_stream_rx.c firmware/cdc_stream_rx.h firmware/hm_copy.h | build
	$(CLANG) --target=arm-none-eabi -mcpu=cortex-m4 -mthumb -ffreestanding -fno-builtin $(CPPFLAGS) $(CFLAGS) -c firmware/cdc_stream_rx.c -o $@
build/test_transport: firmware/cdc_stream_rx.c firmware/cdc_stream_rx.h firmware/hm_copy.h tests/test_transport.c | build
	$(CC) $(CPPFLAGS) $(CFLAGS) firmware/cdc_stream_rx.c tests/test_transport.c -o $@
test: all | build/test-results
	$(PYTHON) tests/test_prototype.py
	$(PYTHON) -m unittest tests.test_host_ports tests.test_extract_firmware
	./build/test_transport
prepare:
	$(PYTHON) tools/extract_firmware.py --download
firmware:
	$(PYTHON) tools/build_patch.py --bulk512 --fast --release --stock-version
check: test firmware
	$(PYTHON) -m unittest tests.test_dfu_update
	$(PYTHON) tests/test_patch_startup.py
	$(PYTHON) tests/test_patched_firmware.py
	$(PYTHON) tests/test_pipeline.py
	$(PYTHON) tests/test_usb512.py
	$(PYTHON) tests/test_readback_backpressure.py
