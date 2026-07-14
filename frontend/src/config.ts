/**
 * Centralised runtime configuration sourced from Vite environment variables.
 * All variables must be prefixed with VITE_ to be exposed to the browser bundle.
 */

export const config = {
  apiUrl: import.meta.env.VITE_API_URL as string,
  mapStyleUrl: import.meta.env.VITE_MAP_STYLE_URL as string,
} as const;
