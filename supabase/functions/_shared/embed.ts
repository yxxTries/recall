// Embeddings with Supabase's built-in gte-small (384 dimensions, English, 512 tokens), inside the Edge Function.

// deno-lint-ignore no-explicit-any
declare const Supabase: any

const MAX_CHARS = 1500 // gte-small reads at most 512 tokens
let session: { run: (text: string, options: object) => Promise<number[]> } | null = null

export async function embed(text: string): Promise<number[]> {
  session ??= new Supabase.ai.Session('gte-small')
  return await session!.run(text.slice(0, MAX_CHARS), { mean_pool: true, normalize: true })
}

// pgvector reads '[0.1,0.2,...]'
export const vector = (v: number[]) => JSON.stringify(v)
