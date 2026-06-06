cmake_minimum_required(VERSION 3.13)
include($ENV{PICO_SDK_PATH}/external/pico_sdk_import.cmake)

project(pico_taf C CXX ASM)
set(CMAKE_C_STANDARD 11)
pico_sdk_init()

add_executable(pico_taf
    main.c
)

target_link_libraries(pico_taf
    pico_stdlib
    hardware_uart
    hardware_gpio
)

# 关键：禁用CRLF转换，确保TAP二进制协议帧完整性
target_compile_definitions(pico_taf PRIVATE
    PICO_STDIO_ENABLE_CRLF_SUPPORT=0
)

pico_add_extra_outputs(pico_taf)
pico_enable_stdio_usb(pico_taf 1)
pico_enable_stdio_uart(pico_taf 0)