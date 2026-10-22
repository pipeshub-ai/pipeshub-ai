# S3 connector: IAM role (AssumeRole) authentication

Choose the **IAM_ROLE** auth type when you create an S3 connector. PipesHub then reads S3 with temporary credentials from STS `AssumeRole`. It does not read S3 with a long-lived key.

The connector needs these fields:

- **Access Key** and **Secret Key**: a base key that can only call `sts:AssumeRole`. PipesHub uses it only to call STS. Give it no S3 access.
- **Role ARN**: the role in the bucket's account that PipesHub assumes.
- **External ID** (optional): the value in the `sts:ExternalId` condition of the role's trust policy. Use one if a third party runs your PipesHub.

PipesHub never uses the AWS default credential chain for this connector. On EKS that chain is the role for PipesHub's own storage bucket.

## 1. Policy for the base key

Attach this policy to the IAM user that owns the base key:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": "arn:aws:iam::<BUCKET_ACCOUNT_ID>:role/<ROLE_NAME>"
    }
  ]
}
```

## 2. Trust policy on the role

Put this trust policy on the role in the bucket's account. Remove the `Condition` block if you do not use an external ID.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "AWS": "arn:aws:iam::<BASE_KEY_ACCOUNT_ID>:user/<BASE_KEY_USER>" },
      "Action": "sts:AssumeRole",
      "Condition": { "StringEquals": { "sts:ExternalId": "<EXTERNAL_ID>" } }
    }
  ]
}
```

## 3. Permission policy on the role

Give the role the read-only S3 policy from the S3 connector page: https://docs.pipeshub.com/connectors/s3/s3. The connector does not need write access.

## What to expect

- The STS session name is `pipeshub-s3-<connectorId>`. CloudTrail shows it, so you can trace calls to one connector.
- STS renews the temporary role credentials before they expire. If you rotate the base access key, update it in PipesHub.
- If STS refuses the role (wrong ARN, wrong external ID, or a trust policy that does not name the base key), the connection test fails. You get a "Connection test failed" notification with the STS `AccessDenied` message.
