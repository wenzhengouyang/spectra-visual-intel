import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from spectra_agent.image_generation import prepare, install, record_search

class ImageGenerationTest(unittest.TestCase):
    def test_model_output_required_and_survives_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            issue = {'editorial_stories': [{'story_id':'a','article_type':'core_event','headline':'图像编辑模型更新','dek':'保持主体一致性','cover_image':{'kind':'editorial_diagram'}}]}
            (root/'editorial-issue.json').write_text(json.dumps(issue))
            (root/'rolling-digest.html').write_text('<!-- ISSUE_DATA_START --><script></script><!-- ISSUE_DATA_END -->')
            self.assertEqual(prepare(root)['status'], 'pending')
            asset = root/'test.png'
            Image.new('RGB',(1280,720)).save(asset)
            with self.assertRaises(ValueError):
                install(root,'a',asset)
            record_search(root, 'a', {tier: {'result':'unavailable','reason':'No relevant usable image','checked_urls':['https://example.com/news']} for tier in ('news_original','official')})
            install(root,'a',asset)
            queue = prepare(root)
            self.assertEqual(queue['status'],'completed')
            current = json.loads((root/'editorial-issue.json').read_text())
            self.assertEqual(current['editorial_stories'][0]['cover_image']['review_status'],'pending')
            (root/queue['jobs'][0]['url']).write_bytes(b'changed')
            self.assertEqual(prepare(root)['status'],'pending')
    def test_short_briefs_do_not_require_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'editorial-issue.json').write_text(json.dumps({'editorial_stories':[{'article_type':'brief'}]}))
            self.assertEqual(prepare(root)['jobs'],[])

    def test_original_image_is_reused_without_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'editorial-issue.json').write_text(json.dumps({'editorial_stories':[{'story_id':'a','article_type':'brief','editorial_tier':'industry_signal','headline':'模型更新'}]}))
            (root/'rolling-digest.html').write_text('<!-- ISSUE_DATA_START --><script></script><!-- ISSUE_DATA_END -->')
            prepare(root)
            asset = root/'original.png'
            Image.new('RGB',(1280,720)).save(asset)
            install(root,'a',asset,origin='news_original',source_url='https://example.com/news',credit='Publisher',usage_basis='Permission granted')
            self.assertEqual(prepare(root)['status'],'completed')
            cover=json.loads((root/'editorial-issue.json').read_text())['editorial_stories'][0]['cover_image']
            self.assertEqual(cover['kind'],'source')
            self.assertEqual(cover['review_status'],'pending')
            job=prepare(root)['jobs'][0]
            (root/job['url']).write_bytes(b'corrupt')
            recovered=prepare(root)['jobs'][0]
            self.assertEqual(recovered['status'],'pending')
            self.assertEqual(recovered['provider'],'source_search')
            with self.assertRaises(ValueError):
                record_search(root,'a',{tier:{'result':'unavailable','reason':'none','checked_urls':'not-a-list'} for tier in ('news_original','official')})
    def test_industry_signal_is_an_image_card(self):
        from spectra_agent.image_review import pending_covers
        from spectra_agent.publication_quality import publication_quality_errors
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            story={'story_id':'signal','article_type':'brief','editorial_tier':'industry_signal','headline':'模型更新开放测试','dek':'发布新的模型能力与接口','cover_image':{'kind':'editorial_diagram','review_status':'pending'}}
            issue={'editorial_stories':[story]}
            (root/'editorial-issue.json').write_text(json.dumps(issue))
            self.assertEqual([j['story_id'] for j in prepare(root)['jobs']],['signal'])
            self.assertEqual(prepare(root)['jobs'][0]['provider'],'source_search')
            self.assertEqual([j['story_id'] for j in pending_covers(issue)],['signal'])
            self.assertTrue(any('cover_missing' in e for e in publication_quality_errors(issue,{},asset_root=root)))
