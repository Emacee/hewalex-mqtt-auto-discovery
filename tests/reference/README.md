# Reference implementation (test only)

`hewalex_geco/` is an unmodified copy of the GECO protocol library from
[mvdklip/Domoticz-Hewalex](https://github.com/mvdklip/Domoticz-Hewalex)
(commit `ab3f25c`, GPL-3.0), which has been used against real PCWU heat pumps
for years.

The tests use it as an independent oracle: frames built by the add-on must be
byte-identical to the ones this library builds, and register values decoded by
the add-on must equal what this library decodes from the same bytes. It is not
shipped in the add-on image.
