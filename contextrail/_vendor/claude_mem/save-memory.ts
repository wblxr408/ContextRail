// SPDX-License-Identifier: Apache-2.0
// Selected literal validation/observation construction from persistMemory.
export function normalizeExplicitMemory(input: {text:string; title?:string}) {
  const text = input.text.trim();
  if (!text) throw new Error('Memory text must not be empty');
  const { title } = input;
  const metadata = undefined;
  const observation = {
    type: 'discovery', title: title?.trim() || text.substring(0, 60).trim() + (text.length > 60 ? '...' : ''),
    subtitle: 'Manual memory', facts: [] as string[], narrative: text, concepts: [] as string[],
    files_read: [] as string[], files_modified: [] as string[], metadata: metadata ? JSON.stringify(metadata) : null,
  };
  return observation;
}
