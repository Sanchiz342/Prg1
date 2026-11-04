const ALPHABET = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz';

/** Random base-62 id from the platform CSPRNG (browser and Node). 12 chars ≈ 71 bits. */
export function randomId(length = 12): string {
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  let out = '';
  for (const b of bytes) out += ALPHABET[b % 62]; // 256 % 62 bias is negligible for ids
  return out;
}

/** Board ids double as the edit permission, so they are longer: 22 chars ≈ 130 bits. */
export const newBoardId = () => randomId(22);
