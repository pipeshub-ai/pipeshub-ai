import os
import logging
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Custom error classes matching the Node.js implementation
class EncryptionError(Exception):
    def __init__(self, message, detail=None):
        super().__init__(f"{message}: {detail}" if detail else message)

class DecryptionError(Exception):
    def __init__(self, message, detail=None):
        super().__init__(f"{message}: {detail}" if detail else message)

class InvalidKeyFormatError(Exception):
    def __init__(self, message):
        super().__init__(message)

# Setup logger to mimic Logger service in Node.js
logger = logging.getLogger("Encryption Service")
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)


class EncryptionService:
    _instance = None

    def __init__(self, algorithm: str, secret_key: str):
        # In this example, algorithm should be "aes-256-gcm"
        self.algorithm = algorithm
        self.secret_key = secret_key  # this is a hex string

    @classmethod
    def get_instance(cls, algorithm: str, secret_key: str):
        if cls._instance is None:
            cls._instance = EncryptionService(algorithm, secret_key)
        return cls._instance

    def encrypt(self, text: str) -> str:
        try:
            # Recommended IV length for GCM is 12 bytes
            iv = os.urandom(12)
            key = bytes.fromhex(self.secret_key)
            aesgcm = AESGCM(key)
            # Encrypt returns ciphertext with the tag appended (last 16 bytes)
            encrypted = aesgcm.encrypt(iv, text.encode('utf-8'), None)
            # Split ciphertext and auth tag
            ciphertext = encrypted[:-16]
            auth_tag = encrypted[-16:]
            # Return the encrypted string as "iv:ciphertext:authTag"
            return f"{iv.hex()}:{ciphertext.hex()}:{auth_tag.hex()}"
        except Exception as e:
            logger.error("Encryption failed", exc_info=True)
            raise EncryptionError("Encryption failed", str(e))

    def decrypt(self, encrypted_text: str) -> str:
        if encrypted_text is None:
            raise DecryptionError("Decryption failed, encrypted text is None")
        try:
            # For AES-256-GCM, expect format "iv:ciphertext:authTag"
            parts = encrypted_text.split(':')
            if len(parts) != 3:
                raise InvalidKeyFormatError("Invalid encrypted text format; expected format iv:ciphertext:authTag")
            iv_hex, ciphertext_hex, auth_tag_hex = parts
            iv = bytes.fromhex(iv_hex)
            ciphertext = bytes.fromhex(ciphertext_hex)
            auth_tag = bytes.fromhex(auth_tag_hex)
            key = bytes.fromhex(self.secret_key)
            # Recombine ciphertext and auth tag as expected by AESGCM.decrypt
            combined = ciphertext + auth_tag
            aesgcm = AESGCM(key)
            decrypted = aesgcm.decrypt(iv, combined, None)
            return decrypted.decode('utf-8')
        except Exception as e:
            logger.error("Decryption failed", exc_info=True)
            raise DecryptionError("Decryption failed, could be due to different encryption algorithm or secret key", str(e))


# Example usage:
if __name__ == "__main__":
    # Replace these with your actual values.
    secret_key = "47eabdd76483a14975e4f6349cbd6812992f9d10cdac3c388a147206e69bed3b"
    service = EncryptionService.get_instance("aes-256-gcm", secret_key)

    # Decrypt the message back
    try:
        encrypted = "e75153450322aef6d4e820bf:bfe5adc59ce3d67de8febb9648e230f4a96e1480d5b5395c99efead45b36e4cadd253f01c572b865923be608f65e87730c8fc369c07975b5a5eb5a3dacfc3f119d6293e58a25fa181bee5bb798114a0dbab5839b560fce657de9da68f9ed0094f4a0064645bf521ad31c7accfee94c6b1a4de3b5f6e263971ae45f705a84f62497b52b127fb17ed025d83f876d166707dfd74a9c812d0dd3e759811743f4bba70163bf1e4c1c443076474ac43384d8f41fbc77675a4976930751f56fa106509fe562ecb9648b6f85ae41870c9bdbf8afeca2a66aff4744316a983f81bb5f35938e63d0626a42bd5101631bbc876e5f7f2085493ccbef2021319f7a5a23f0b987db858be2960c75a184697b8d320ab0c8d1e8176b6a81ff80a97b112f9b172fb4a59cd7d53425aae1e666416134a6fdb02cce87b65beb56ec976eed731e88b516bc29b2484c09bb1067b5e3a428f5433523f9f27b34ce4d04c9cae28b5dd85ce953a94bb917cfa0f00d64964b6cba63784f9821dc77f5d72a7a4de0f05b30deca43f2aa84c3f1675ced35f8ec2a3461925e5a06f90da46462cd48ea3e5c1d798086ed92c1bb553aa7fa7146c6326b51c81ad1352c2b28b5e5ed81026f51d30b3d31384537a8c97df9e45554e7823fc14be7a0527004d3cd4a179209e7b8b1ef152101d2f70c092a31ed77eb4792b35a51e87648770756d3084cb73c82e40781c4a5e0a0242b6aed13ac4e6c96f3e970ba82ee41ea8c28c69761f3f98a0a723a6efc2dcfe7850a1010d243566843f1e9b80f3f98f08bb3d9f4de7eb88c96ad68b298a050409d294c7654c92038f86cbd0a2e428883993cd0c57b9813660eaaf749588e690046d6b7c04b9b2a64cd185649cb22a73248513c9edc79b675ebe6f45361b325cf627687f41b4f49527b6b038a93528fa126d94e1029c7d7acc69a0a6040fb11c55c88024904bb3e8c81a4d0c392f941ac67f20344f679f981751905cc703847dd2fa088068684b37ae069a4c5e28db82e7d3f954341b3a49b4a2b645411eefc33ba0aa2de14112f9f675dfbb1a5bcd65482141ebe3cbd526c2a8e07c6b6e06180fd80062f4c753f13c7180b93dd642827b34f1b07ec1c71d6f3b1877d8b057915a1003ff8473f76e15d22b49e0e0f83b61734425c1d38d6038905d8cf2ca517a754cad7e56fd96085fc49af55b36264256bc9c7501d3cca6c0acb43a198caab2948516188b672d5a8f2c0f17183999558992704bbe385e3caab7bdad792c53ab56aa53afd3a039dd5835cd3f1ddfd6c6542471c632a307d397af3158c56e51f6580eff442fcd774eeecd3a1b50495271b1ec8501ea8b46ffc66e335b14c5de24d3429f82552d99eda77b768cc5b4bcfb04deefeda451b0e054fba3941b02f62011f00cde8216a4938564844a213350f6ff6e5e198a0ec58841dc2be54e962890cbcf721cf2e9650c8d3aa13e27c5d680e723ac2096846bdf7e2b3039b50f47e9e0f6d276970e3fe7f982cd61691d68e8438f6ea4ffcbf176cb975be4326145b52feed4e787105d7ee363ad55862a9b5842552ac76bb21c4d0448c7d0dfa6b8af90cad0cc0b237b6813be25146e893110775d1187b228ce48c44d30d5c330a4489acad893224aa0d5ab77439b89287872eb430b6230492a9b73fb858c724073b005c01c9702905b98b526f1c08118a2c0de22aa2109c89ea59e95195e8ad446032c9975a812027cd3dcca8493ab925e2284e3fef20fb06544643d814726b9a568243cfa9028886efcee4983a3be4a37224c970db4d66e827d650cba716aa2f48df357e18cc1a8863c60d05bcf3b5bd16ec9ca071388a8b7e545e4c73bff9eb3d4a2e173d86bd832ba6dc3f3673:49b74f639ef887b67cfcb09eb07bd30b"
        decrypted = service.decrypt(encrypted)
        print("Decrypted:", decrypted)
    except Exception as error:
        print(error)
