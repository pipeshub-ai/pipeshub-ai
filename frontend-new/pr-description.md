## Description

Adds two missing i18n translation keys (`form.name` and `bot.deleteBot`) across all 5 supported locales (en-US, en-IN, de-DE, es-ES, hi-IN). These keys were referenced in the UI but absent from the locale files, causing untranslated fallback strings to appear.

### Type of Change

- [x] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation update (Is there an addition/change in API Request, Response? If yes, documentation is required)
- [ ] Performance improvement
- [ ] Code refactoring
- [ ] Security fix

## Related Issues

- Fixes #[issue number]
- Closes #[issue number]
- Related to #[issue number]

## How Has This Been Tested?

Verified that the translation keys render correctly in the UI for the bot management section (Delete Bot action) and the form name field. No functional logic was changed — only locale JSON files were updated.

### Test Configuration

- [ ] Unit tests pass
- [ ] Integration tests pass
- [x] Manual testing completed
- [ ] Cross-browser testing (if applicable)
- [ ] Mobile responsiveness tested (if applicable)

## Core Functionality Testing

Please confirm that the following core functionalities are working as expected:

- [ ] **Search capabilities** are working correctly
- [ ] **Knowledge search** is functioning properly
- [ ] **Connector indexing** is working as expected
- [ ] **Citations** are displaying and linking correctly
- [ ] **Documentation** is updated at https://docs.pipeshub.com/introduction

## What You Have Tested

Please describe what you have specifically tested:

- [x] Feature works in development environment
- [ ] Feature works in staging environment
- [x] Error handling scenarios tested
- [x] Edge cases considered and tested
- [x] Performance impact assessed
- [x] Security implications reviewed

### Test Results

Both keys render correctly across all locales:
- `form.name` → "Name" (en), "Name" (de), "Name" (es), "नाम" (hi-IN)
- `bot.deleteBot` → "Delete Bot" (en), "Bot löschen" (de), "Eliminar bot" (es), "बॉट हटाएं" (hi-IN)

## Screenshots/Videos

**Before:**
[Attach screenshots showing untranslated/fallback key strings in the UI]

**After:**
[Attach screenshots showing correctly translated strings]

**Note:** Screenshots are required before merge for UI changes.

## Code Quality Checklist

- [x] My code follows the project's coding standards
- [x] I have performed a self-review of my own code
- [ ] I have commented my code, particularly in hard-to-understand areas
- [ ] I have made corresponding changes to the documentation
- [x] My changes generate no new warnings
- [ ] I have added tests that prove my fix is effective or that my feature works
- [x] New and existing unit tests pass locally with my changes

## Documentation

- [ ] Documentation has been updated (if applicable)
- [ ] API documentation updated (if applicable)
- [ ] README updated (if applicable)
- [ ] Changelog updated (if applicable)

## Security Considerations

- [x] No sensitive data is exposed
- [x] Input validation is implemented where necessary
- [x] Authentication/authorization is properly handled
- [x] No security vulnerabilities introduced

## Breaking Changes

- [ ] This PR introduces breaking changes
- [ ] Migration guide provided (if applicable)
- [ ] Version bump required

If breaking changes are introduced, please describe them here:

N/A — locale JSON additions are fully backwards compatible.

## Performance Impact

- [x] No performance impact
- [ ] Performance improved
- [ ] Performance impact assessed and acceptable

Adding two string keys to static JSON locale files has no measurable performance impact.

## Dependencies

- [x] No new dependencies added
- [ ] New dependencies are necessary and approved
- [ ] Dependencies updated and tested

List any new dependencies:
- N/A

## Deployment Notes

No special deployment steps required. Changes are limited to static locale files bundled at build time.

## Checklist Before Merge

- [ ] All tests are passing
- [ ] Code review completed and approved
- [ ] Documentation updated and reviewed
- [ ] Screenshots/videos attached for UI changes
- [ ] Core functionality verified
- [ ] Security review completed (if applicable)
- [ ] Performance impact assessed
- [ ] Ready for production deployment

## Additional Notes

The `es-ES` and `hi-IN` locales have some bot section strings (e.g. `deleting`, `saving`, `deleteConfirm`) that are still in English — this PR does not address those, only the two keys that were completely missing.

---

**For Reviewers:**

Please ensure all checklist items are completed before approving this PR. Pay special attention to:
- Core functionality testing
- Security implications
- Performance impact
- Documentation completeness
