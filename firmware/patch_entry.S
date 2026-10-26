.syntax unified
.cpu cortex-m4
.thumb
.section .text.patch_dispatch,"ax",%progbits
.global patch_dispatch
.type patch_dispatch,%function
.thumb_func
patch_dispatch:
  /* Called by a branch from the original cmd4 switch, not a BL. Preserve
     dispatcher's live r0-r3/r12; original function's LR is already saved. */
  push {r0-r3,r12,lr}
  bl patch_command
  pop {r0-r3,r12,lr}
  ldr pc, =0x08034bc3
.size patch_dispatch,.-patch_dispatch
#ifdef HM_BULK512
.section .text.patch_usb_buffer_init,"ax",%progbits
.global patch_usb_buffer_init
.type patch_usb_buffer_init,%function
.thumb_func
patch_usb_buffer_init:
  ldr r1, =usb_buffer
  ldr r0, =0x08014335
  bx r0
.size patch_usb_buffer_init,.-patch_usb_buffer_init
.section .text.stock_usb_descriptor,"ax",%progbits
.global stock_usb_descriptor
.type stock_usb_descriptor,%function
.thumb_func
stock_usb_descriptor:
  /* Relocate the exact four-byte prologue overwritten by our entry hook. */
  push {r4-r10,lr}
  ldr pc, =0x0801440d
.size stock_usb_descriptor,.-stock_usb_descriptor
#endif
