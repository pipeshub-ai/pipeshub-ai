import { expect } from 'chai'
import {
  hasSignedUrlQuery,
  stripSignedUrlLinks,
} from '../../../../src/modules/enterprise_search/utils/signed-url'

describe('signed-url helpers', () => {
  describe('hasSignedUrlQuery', () => {
    for (const url of [
      'https://a.blob.core.windows.net/c/f?sv=2024&sig=abc%3D',
      'https://b.s3.amazonaws.com/f?X-Amz-Signature=ab',
      'https://storage.googleapis.com/b/f?x-goog-signature=ab',
      'https://h/f?a=1&Signature=ab',
    ]) {
      it(`flags ${url}`, () => expect(hasSignedUrlQuery(url)).to.equal(true))
    }
    for (const url of [
      'https://example.com/f.csv',
      'https://example.com/sig=abc/f',
      'https://example.com/f?signed=1&sigma=2&design=3',
      'http://h/record/r1/preview#blockIndex=0',
    ]) {
      it(`ignores ${url}`, () => expect(hasSignedUrlQuery(url)).to.equal(false))
    }
  })

  describe('stripSignedUrlLinks', () => {
    it('removes signed links and images, leaving surrounding text', () => {
      expect(
        stripSignedUrlLinks('a [x](https://h/f?sig=1) b ![i](https://h/i?X-Amz-Signature=2) c'),
      ).to.equal('a  b  c')
    })

    it('leaves normal links, citations and record markers untouched', () => {
      const text =
        'Hi [site](https://example.com/?q=sig) [1](http://h/record/r/preview#blockIndex=2) [a](b) `code(x)`\n::artifact[f](record:r1){a/b|d|r1||2}'
      expect(stripSignedUrlLinks(text)).to.equal(text)
    })

    it('does not swallow text between two links when only the second is signed', () => {
      expect(
        stripSignedUrlLinks('[a](https://ok.com) middle [b](https://h/f?sig=1) end'),
      ).to.equal('[a](https://ok.com) middle  end')
    })

    it('replaces bare signed URLs with a placeholder and keeps unsigned ones', () => {
      expect(
        stripSignedUrlLinks('get https://h/f?sig=1&se=2. then https://example.com/a?b=1 ok'),
      ).to.equal('get [link removed] then https://example.com/a?b=1 ok')
    })

    it('replaces a signed URL left inside an over-long markdown link', () => {
      const url = `https://h/${'a'.repeat(5000)}?X-Amz-Signature=zz`
      const out = stripSignedUrlLinks(`[f](${url}) end`)
      expect(out).to.not.contain('X-Amz-Signature')
      expect(out).to.contain('end')
    })

    it('still removes a signed URL inside a link whose label nests brackets', () => {
      const out = stripSignedUrlLinks('see [the [q3] report](https://b.s3.amazonaws.com/r.pdf?X-Amz-Signature=abc) now')
      expect(out).to.not.contain('X-Amz-Signature')
      expect(out).to.contain('now')
    })

    it('stays fast on pathological input', () => {
      const inputs = [
        '['.repeat(200000),
        '[a]('.repeat(50000),
        `[${'x'.repeat(200000)}`,
        `https://${'a'.repeat(300000)}`,
        '![a](https://h/?'.repeat(20000),
      ]
      for (const input of inputs) {
        const start = Date.now()
        stripSignedUrlLinks(input)
        expect(Date.now() - start).to.be.lessThan(1000)
      }
    })

    it('handles empty content', () => {
      expect(stripSignedUrlLinks('')).to.equal('')
    })
  })
})
