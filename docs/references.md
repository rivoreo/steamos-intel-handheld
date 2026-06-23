# References

## Intel Core Ultra 7 258V

- Intel official product specification page:
  <https://www.intel.com/content/www/us/en/products/sku/240960/intel-core-ultra-7-processor-258v-12m-cache-up-to-4-80-ghz/specifications.html>

The relevant power model for this project is Intel's Base Power / Maximum Turbo
Power split. For the MSI Claw 8 AI+ device tested here, the SteamOS UI-selected
TDP maps to the long-term package limit, while the profile `max_w` maps to the
short-term turbo limit.

## Linux powercap and Intel RAPL

- Linux kernel powercap documentation:
  <https://www.kernel.org/doc/html/latest/power/powercap/powercap.html>

The kernel interface exposes RAPL package constraints as named power limits with
time windows. On the tested device, the relevant package constraints are:

- `constraint_0_name=long_term`
- `constraint_1_name=short_term`
- `constraint_2_name=peak_power` on the MMIO powercap path

## First device evidence

MSI Claw 8 AI+ A2VM, Intel Core Ultra 7 258V:

- SteamOS UI TDP setting: 17W
- SteamOS Manager central `TdpLimit`: 17W
- remote provider `TdpLimit`: 17W
- RAPL `long_term`: 17W
- prototype RAPL `short_term`: 21W

The formal project changes the default short-term value to 37W for this profile,
because that follows Intel's Maximum Turbo Power model. Lower PL2 values should
be explicit device policy, not a hidden multiplier.
