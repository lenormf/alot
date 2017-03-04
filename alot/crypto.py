# Copyright (C) 2011-2012  Patrick Totzke <patricktotzke@gmail.com>
# This file is released under the GNU GPL, version 3 or a later revision.
# For further details see the COPYING file
from __future__ import absolute_import

import gpg

from .errors import GPGProblem, GPGCode


def RFC3156_micalg_from_algo(hash_algo):
    """
    Converts a GPGME hash algorithm name to one conforming to RFC3156.

    GPGME returns hash algorithm names such as "SHA256", but RFC3156 says that
    programs need to use names such as "pgp-sha256" instead.

    :param hash_algo: GPGME hash_algo
    :rtype: str
    """
    # hash_algo will be something like SHA256, but we need pgp-sha256.
    hash_algo = gpg.core.hash_algo_name(hash_algo)
    return 'pgp-' + hash_algo.lower()


def get_key(keyid, validate=False, encrypt=False, sign=False,
            signed_only=False):
    """
    Gets a key from the keyring by filtering for the specified keyid, but
    only if the given keyid is specific enough (if it matches multiple
    keys, an exception will be thrown).

    If validate is True also make sure that returned key is not invalid,
    revoked or expired. In addition if encrypt or sign is True also validate
    that key is valid for that action. For example only keys with private key
    can sign. If signed_only is True make sure that the user id can be trusted
    to belong to the key (is signed). This last check will only work if the
    keyid is part of the user id associated with the key, not if it is part of
    the key fingerprint.

    :param keyid: filter term for the keyring (usually a key ID)
    :type keyid: str
    :param validate: validate that returned keyid is valid
    :type validate: bool
    :param encrypt: when validating confirm that returned key can encrypt
    :type encrypt: bool
    :param sign: when validating confirm that returned key can sign
    :type sign: bool
    :param signed_only: only return keys  whose uid is signed (trusted to
        belong to the key)
    :type signed_only: bool
    :rtype: gpg key object
    """
    ctx = gpg.Context()
    try:
        key = ctx.get_key(keyid)
        if validate:
            validate_key(key, encrypt=encrypt, sign=sign)
    except gpg.errors.GPGMEError as e:
        if e.getcode() == gpg.errors.AMBIGUOUS_NAME:
            # When we get here it means there were multiple keys returned by
            # gpg for given keyid. Unfortunately gpgme returns invalid and
            # expired keys together with valid keys. If only one key is valid
            # for given operation maybe we can still return it instead of
            # raising exception
            keys = list_keys(hint=keyid)
            valid_key = None
            for k in keys:
                try:
                    validate_key(k, encrypt=encrypt, sign=sign)
                except GPGProblem:
                    # if the key is invalid for given action skip it
                    continue

                if valid_key:
                    # we have already found one valid key and now we find
                    # another? We really received an ambiguous keyid
                    raise GPGProblem(("More than one key found matching " +
                                      "this filter. Please be more " +
                                      "specific (use a key ID like " +
                                      "4AC8EE1D)."),
                                     code=GPGCode.AMBIGUOUS_NAME)
                valid_key = k

            if not valid_key:
                # there were multiple keys found but none of them are valid for
                # given action (we don't have private key, they are expired
                # etc)
                raise GPGProblem(
                    "Can not find usable key for \'" +
                    keyid +
                    "\'.",
                    code=GPGCode.NOT_FOUND)
            return valid_key
        elif e.getcode() == gpg.errors.INV_VALUE or e.getcode() == gpg.errors.EOF:
            raise GPGProblem("Can not find key for \'" + keyid + "\'.",
                             code=GPGCode.NOT_FOUND)
        else:
            raise e
    if signed_only and not check_uid_validity(key, keyid):
        raise GPGProblem("Can not find a trusworthy key for '" + keyid + "'.",
                         code=GPGCode.NOT_FOUND)
    return key


def list_keys(hint=None, private=False):
    """
    Returns a list of all keys containing keyid.

    :param keyid: The part we search for
    :param private: Whether secret keys are listed
    :rtype: list
    """
    ctx = gpg.Context()
    return ctx.keylist(hint, private)


def detached_signature_for(plaintext_str, key=None):
    """
    Signs the given plaintext string and returns the detached signature.

    A detached signature in GPG speak is a separate blob of data containing
    a signature for the specified plaintext.

    :param plaintext_str: text to sign
    :param key: gpgme_key_t object representing the key to use
    :rtype: tuple of gpg.results.NewSignature array and str
    """
    ctx = gpg.Context(armor=True)
    if key is not None:
        ctx.signers = [key]
    (sigblob, sign_result) = ctx.sign(plaintext_str, mode=gpg.constants.SIG_MODE_DETACH)
    return sign_result.signatures, sigblob


def encrypt(plaintext_str, keys=None):
    """
    Encrypts the given plaintext string and returns a PGP/MIME compatible
    string

    :param plaintext_str: the mail to encrypt
    :param key: gpgme_key_t object representing the key to use
    :rtype: a string holding the encrypted mail
    """
    ctx = gpg.Context()
    ctx.armor = True
    (out, encrypt_result, _) = ctx.encrypt(plaintext_str, recipients=keys, always_trust=True)
    return out


def verify_detached(message, signature):
    '''Verifies whether the message is authentic by checking the
    signature.

    :param message: the message as `str`
    :param signature: a `str` containing an OpenPGP signature
    :returns: a list of :class:`gpg.results.Signature`
    :raises: :class:`~alot.errors.GPGProblem` if the verification fails
    '''
    ctx = gpg.Context()
    try:
        (_, verify_results) = ctx.verify(message, signature)
        return verify_results.signatures
    except gpg.errors.GPGMEError as e:
        raise GPGProblem(e.message, code=e.code)


def decrypt_verify(encrypted):
    '''Decrypts the given ciphertext string and returns both the
    signatures (if any) and the plaintext.

    :param encrypted: the mail to decrypt
    :returns: a tuple (sigs, plaintext) with sigs being a list of a
              :class:`gpg.result.Signature` and plaintext is a `str` holding
              the decrypted mail
    :raises: :class:`~alot.errors.GPGProblem` if the decryption fails
    '''
    ctx = gpg.Context()
    try:
        (plaintext, _, verify_result) = ctx.decrypt(encrypted, verify=True)
    except gpg.errors.GPGMEError as e:
        raise GPGProblem(e.message, code=e.getcode())

    return verify_result.signatures, plaintext


def validate_key(key, sign=False, encrypt=False):
    """Assert that a key is valide and optionally that it can be used for
    signing or encrypting.  Raise GPGProblem otherwise.

    :param key: the GPG key to check
    :type key: gpg key object
    :param sign: whether the key should be able to sign
    :type sign: bool
    :param encrypt: whether the key should be able to encrypt
    :type encrypt: bool

    """
    if key.revoked:
        raise GPGProblem("The key \"" + key.uids[0].uid + "\" is revoked.",
                         code=GPGCode.KEY_REVOKED)
    elif key.expired:
        raise GPGProblem("The key \"" + key.uids[0].uid + "\" is expired.",
                         code=GPGCode.KEY_EXPIRED)
    elif key.invalid:
        raise GPGProblem("The key \"" + key.uids[0].uid + "\" is invalid.",
                         code=GPGCode.KEY_INVALID)
    if encrypt and not key.can_encrypt:
        raise GPGProblem("The key \"" + key.uids[0].uid + "\" can not " +
                         "encrypt.", code=GPGCode.KEY_CANNOT_ENCRYPT)
    if sign and not key.can_sign:
        raise GPGProblem("The key \"" + key.uids[0].uid + "\" can not sign.",
                         code=GPGCode.KEY_CANNOT_SIGN)


def check_uid_validity(key, email):
    """Check that a the email belongs to the given key.  Also check the trust
    level of this connection.  Only if the trust level is high enough (>=4) the
    email is assumed to belong to the key.

    :param key: the GPG key to which the email should belong
    :type key: gpgme.Key
    :param email: the email address that should belong to the key
    :type email: str
    :returns: whether the key can be assumed to belong to the given email
    :rtype: bool

    """
    for key_uid in key.uids:
        if email == key_uid.email and not key_uid.revoked and \
                not key_uid.invalid and key_uid.validity >= 4:
            return True
    return False
