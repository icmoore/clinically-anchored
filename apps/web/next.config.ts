import type { NextConfig } from "next";
import { assertPublicEnvForDeploy } from "./src/lib/required-public-env.ts";

// On Vercel, refuse to build without the NEXT_PUBLIC_* values the browser needs (see the module).
assertPublicEnvForDeploy(process.env);

const nextConfig: NextConfig = {
  /* config options here */
};

export default nextConfig;
