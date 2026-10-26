#ifndef HM_COPY_H
#define HM_COPY_H
#include <stdint.h>
#include <stddef.h>
static inline void hm_copy(uint8_t *dst,const uint8_t *src,size_t n){
#ifdef HM_FAST_COPIES
    /* Both buffers are RAM. Never perform an unaligned word access; the byte
     * fallback also covers arbitrary 63-byte USB fragment boundaries. */
    typedef uint32_t alias_word __attribute__((may_alias));
    if(!(((uintptr_t)dst|(uintptr_t)src)&3u)){
        while(n>=4){*(alias_word*)dst=*(const alias_word*)src;dst+=4;src+=4;n-=4;}
    }
#endif
    while(n--)*dst++=*src++;
}
#endif
