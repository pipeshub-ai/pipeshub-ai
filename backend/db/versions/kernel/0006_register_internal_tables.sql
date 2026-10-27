-- Every relation outside schema kernel is registered; the profile row holds no org data.
SELECT kernel.register_internal('platform.deployment_profile');
