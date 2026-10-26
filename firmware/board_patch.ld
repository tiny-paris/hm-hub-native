MEMORY {
  PATCH (rx) : ORIGIN = 0x0807dec0, LENGTH = 0x2140
  STATE (rw) : ORIGIN = 0x2001ef20, LENGTH = 0x2e0
}
SECTIONS {
 .text : { *(.text*) *(.rodata*) } > PATCH
 .ARM.exidx : { *(.ARM.exidx*) } > PATCH
 .bss (NOLOAD) : { __patch_bss_start = .; *(.bss*) *(COMMON) __patch_bss_end = .; } > STATE
 .usb_rx (NOLOAD) : { *(.usb_rx*) } > STATE
 /DISCARD/ : { *(.comment) *(.ARM.attributes) }
}
ASSERT(SIZEOF(.data) == 0, "patch cannot contain initialized writable data")
