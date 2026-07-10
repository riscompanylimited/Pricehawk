# Project-Specific Instructions for Claude Code

## Session Documentation Protocol

**IMPORTANT**: After implementing any significant changes, features, or bug fixes, you MUST create comprehensive session documentation in the `ai_sum/sessions/` directory.

### When to Create Session Documentation

Create documentation after:
- Implementing new features
- Making significant bug fixes
- Refactoring code
- Making database schema changes
- Adding or modifying API endpoints
- UI/UX improvements
- Performance optimizations
- Any work that spans multiple files or components

### Documentation Format

1. **Filename**: Use format `YYYY-MM-DD_descriptive-title.md`
   - Example: `2026-02-02_export-enhancements-and-price-history-ui.md`

2. **Required Sections**:
   ```markdown
   # [Descriptive Title]

   **Date**: YYYY-MM-DD
   **Branch**: [branch name]
   **Status**: [In Progress/Completed/Blocked]

   ## Overview
   Brief summary of what was accomplished

   ## Changes Made

   ### 1. [Feature/Fix Name]
   **Files Modified**:
   - `path/to/file.ts` (lines X-Y)

   **Changes**:
   - Bullet points of what changed
   - Include code snippets for complex logic

   ## Summary of Files Modified
   List all files with line numbers and brief description

   ## Testing Checklist
   - [ ] Feature X works
   - [ ] No regressions

   ## Key Technical Decisions
   Explain why certain approaches were chosen

   ## User Feedback Addressed
   List original user requests with ✅ checkmarks

   ## Commit Information
   Include commit hash and message
   ```

3. **Code References**: Use file:line format (e.g., `backend/main.py:753`)

4. **Screenshots**: Reference any relevant screenshots or UI changes

### Documentation Workflow

1. Implement the requested changes
2. Test the implementation
3. Create git commit
4. **IMMEDIATELY** create session documentation in `ai_sum/sessions/`
5. Summarize to user what was documented

### Existing Documentation Structure

- `ai_sum/SUMMARY.md` - High-level project overview (update quarterly)
- `ai_sum/sessions/` - Individual session logs (create after each session)
- Keep documentation detailed enough for future reference

## Project Context

- **Tech Stack**: Next.js 14, Python FastAPI, PostgreSQL, Railway deployment
- **Environments**:
  - SIT: pricehawk-uat.up.railway.app
  - UAT: pricehawk-uat.up.railway.app
  - PRD: pricehawk-production-0e37.up.railway.app
- **Base Product**: Thai Watsadu (TWD) - all other retailers match against this
- **Key Retailers**: HomePro, MegaHome, Do Home, Boonthavorn, Global House

## Code Style Preferences

- Use TypeScript interfaces for type safety
- Prefer async/await over promises
- Use Tailwind CSS for styling
- Follow existing naming conventions (snake_case for backend, camelCase for frontend)
- Always include proper error handling

## Commit Message Format

```
Brief summary line (under 70 chars)

- Detailed point 1
- Detailed point 2
- Detailed point 3

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
```
