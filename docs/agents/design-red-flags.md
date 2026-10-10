# Design red flags

Read this before shared-contract work (step 2 of the agent loop in [CONTRIBUTING.md](../../CONTRIBUTING.md)), and during architecture planning.

Several agents work in this repo at once. Assume the next contributor is an agent that sees only the files it opened, copies the nearest example, and takes the shortest path that compiles. Prefer designs where a change that looks right from one file is right for the whole repo.

Adapted from PStack's `architect` skill and its `design-red-flags` reference. See [Credits](#credits).

## Before changing a shared contract

- **Write the caller's usage first,** then derive the types and signatures from it.
- **When the choice is about ownership, state, or module boundaries,** sketch at least two shapes that are structurally different, not one shape with different names. Pick one and write down why.
- **If implementation keeps fighting the design, redesign it.** One awkward edge case doesn't count. A pattern does: the same workaround in unrelated places, types that need casts or `any` to compile, or callers that have to know the module's internal rules.

## Red flags that break multi-agent repos

**Split ownership.** More than one module writes the same state or keeps its own copy of it. An agent that edits one writer can't see the others, so their rules drift apart. Give each piece of state one owner. Other modules read it or ask the owner to change it.

**Two ways to do one task.** An agent copies whichever way it finds first, so every way keeps gaining callers. Keep one way. Move the callers over and delete the old way in the same PR. Don't leave a compatibility layer behind.

**Importable internals.** If a caller can import a module's internals, an agent will, and they become part of the interface. Make internals unreachable from outside the module, so an import from outside fails the build.

**Hand-synced lists.** Two or more places list the same items, so adding one means editing every list. An agent that sees one list updates only that one. Keep one list and derive the others. If a list can't be derived, make the build fail when the lists disagree.

## Other red flags

**Shallow module.** A large interface that hides little. Callers have to coordinate several methods to complete one operation. Prefer a small interface backed by substantial behavior.

**Information leakage.** The same internal decision (a format, a policy, a protocol detail) shows up in several modules, so changing it needs coordinated edits. Parse external data into the app's own types at the boundary, and keep storage and wire formats private.

**Temporal decomposition.** Modules split by execution order (load, then validate, then save) instead of by the knowledge they own. Group code around what it owns.

**Pass-through method.** A method that forwards the same arguments to another method with the same shape. Remove it, unless it adds policy or adaptation.

## Credits

Adapted from [PStack](https://github.com/cursor/plugins/tree/e43c7ee26e0038c6c1fa8380dd34ce86ff94cb2a/pstack/skills/architect) (`architect`: `SKILL.md`, `references/design-red-flags.md`), MIT License, Copyright (c) 2026 Lauren Tan. See [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md).
