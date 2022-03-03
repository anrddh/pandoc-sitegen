from typing import *
import subprocess
import os
import sys
from pathlib import Path

import yaml
import chevron

# define custom tag handler for yaml
# originally from 
# https://stackoverflow.com/questions/5484016/how-can-i-do-string-concatenation-or-string-replacement-in-yaml#5954748
def join(loader, node):
	seq = loader.construct_sequence(node)
	return ''.join([str(i) for i in seq])

# register the tag handler
yaml.add_constructor('!join', join)


class PandocMarkdown(object):
	def __init__(
			self, 
			delim : str = '---',
			loader : Callable[[str],Dict] = yaml.safe_load,
			writer : Callable[[Dict],str] = lambda x : yaml.dump(x, default_flow_style = None, sort_keys = False),
		) -> None:
		
		self.delim : str = delim
		self.loader : Callable[[str],Dict] = loader
		self.writer : Callable[[Dict],str] = writer

		self.initialized : bool = False
		# get the first section and parse as yaml
		self.frontmatter : Dict[str, Any] = dict()
		# get the content
		self.content : str = ''

	def load_file(self, filename : str) -> None:
		"""load a file into the pandoc markdown object
		
		### Parameters:
		 - `filename : str`   
		   the filename to load
		"""

		with open(filename, "r", encoding = "utf-8") as f:
			# split the document by yaml file front matter
			sections : List[str] = f.read().split(self.delim)

		# check the zeroth section is empty
		if sections[0].strip():
			raise ValueError(f"file does not start with yaml front matter, found at start of file: {sections[0]}")
		
		if len(sections) < 3:
			raise ValueError(f'missing sections in file {filename}, check delims')

		# get the first section and parse as yaml
		self.frontmatter : Dict[str, Any] = self.loader(sections[1])
		# get the content
		self.content : str = self.delim.join(sections[2:])

		self.initialized : bool = True

	@staticmethod
	def create_from_file(filename : str, **kwargs) -> 'PandocMarkdown':
		pmd : PandocMarkdown = PandocMarkdown(**kwargs)
		pmd.load_file(filename)
		return pmd

	def dumps(self) -> str:
		if (self.frontmatter is None) or (self.content is None):
			raise Exception('')

		return '\n'.join([
			self.delim,
			self.writer(self.frontmatter).strip(),
			self.delim,
			self.content.lstrip(),
		])

"""
example config file:
```yaml
# base directories
resources: &RESOURCES_DIR "./resources/"
content: &CONTENT_DIR "./content/"
public: &PUBLIC_DIR "./public/"

# pandoc stuff
header: !join [*RESOURCES_DIR, "header.html"]
before: !join [*RESOURCES_DIR, "before-body.html"]
after: !join [*RESOURCES_DIR, "after-body.html"]
filters:
  - !join [*RESOURCES_DIR, "pandoc-filters/csv_code_table.py"]
  - !join [*RESOURCES_DIR, "pandoc-filters/dendron_links_html.py"]

# index file stuff
make_index_files: true
generated_index_suffix: "._index.md"
mustache_rerender: true
```"""

CFG : Dict[str, Any] = None

def gen_cmd(plain_path : str, plain_path_out : Optional[str]) -> Tuple[List[str],Path]:
	"""generate the command to run pandoc
	
	### Returns: `Tuple[List[str],Path]`
	 - `List[str]` 
	   command to run pandoc
	 - `Path`
	   the path to the output file
	"""
	if plain_path_out is None:
		plain_path_out = plain_path

	out_path : Path(CFG['public']) / Path(f'{plain_path_out}.html')

	base_cmd : List[str] = [
		'pandoc',
		# '-c', f'"{CSS}"',
		'--include-in-header', CFG['header'],
		'--include-before-body', CFG['before'],
		'--include-after-body', CFG['after'],
		'--mathjax',
		'-f', 'markdown',
		'-t', 'html5',
		'-o', out_path,
		Path(CFG['content']) / Path(f'{plain_path}.md'),
	]

	for filter_path in CFG['filters']:
		base_cmd.extend(['--filter', filter_path])

	return base_cmd, out_path


def get_plain_path(fname : Path) -> Path:
	"""get the plain path from a filename"""

	return Path(str(fname).removesuffix('.md')).relative_to(CFG['content'])

def add_index_page(path_original : Path) -> Path:
	"""process an index page from `path_original` and return the new path

	new path depends on `CFG['generated_index_suffix']`

	TODO: sorting functions for organizing the items in the index pages

	TODO: this will only work for things organized by dotlists, not nested folders
	"""
	# create the new path
	path_new : Path = Path(str(path_original).removesuffix('.md') + CFG['generated_index_suffix'])
	
	# read the existing document
	doc : PandocMarkdown = PandocMarkdown.create_from_file(path_original)

	# if we use a template from a file, append that template to the end of the content
	if 'template_file' in doc.frontmatter:
		with open(doc.frontmatter['template_file'], 'r') as f:
			doc.content += f.read()

	# read the frontmatter of all downstream files
	downstream_frontmatter : List[Dict[str,Any]] = list()
	for downstream_path in path_original.parent.glob(f'{path_original.stem}*'):
		fm_temp : Dict[str,Any] = PandocMarkdown.create_from_file(downstream_path).frontmatter
		fm_temp['__filename__'] = str(downstream_path)
		downstream_frontmatter.append(fm_temp)
	
	# plug the frontmatter into the content using chevron
	new_content : str = chevron.render(doc.content, { 'children': downstream_frontmatter })

	# write the new content
	doc.content = new_content
	with open(path_new, 'w') as f:
		f.write(doc.dumps())

	return path_new




def gen_page(md_path : str) -> None:
	"""generate a single page, putting it in the public directory"""
	# get the original file
	if not os.path.isfile(md_path):
		raise FileNotFoundError(f"{md_path} is not a valid source file")
	
	plain_path : str = get_plain_path(md_path)
	plain_path_out : Optional[str] = None
	doc : PandocMarkdown = PandocMarkdown.create_from_file(md_path)

	# TODO: allow for custom specification of after/before/header in frontmatter
	
	# if it is a special index file, generate the index page
	if CFG['make_index_files']:
		if ('index' in doc.frontmatter) and (doc.frontmatter['index']):
			gen_idx_path : str = add_index_page(Path(md_path))
			plain_path_out = get_plain_path(gen_idx_path)

	# construct and run the command
	print(f"# Generating {plain_path}")
	cmd, out_path = gen_cmd(plain_path, plain_path_out)
	p_out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
	if p_out.returncode != 0:
		raise RuntimeError(f"Failed to generate {plain_path}:\n\n{p_out.stderr.decode('utf-8')}")

	# rerender the page
	if CFG['mustache_rerender']:
		with open(out_path, 'r') as f:
			content : str = f.read()
		content_new : str = chevron.render(content, { 'children': doc.frontmatter })
		with open(out_path, 'w') as f:
			f.write(content_new)

def gen_all_pages() -> None:
	# create all required directories first
	for content_dir in CFG['content'].glob('*'):
		if content_dir.is_dir():
			public_dir : Path = CFG['public'] / content_dir.relative_to(CFG['content'])
			if not public_dir.exists():
				public_dir.mkdir(parents=True)
				print(f"# Created {public_dir}")

	# generate all pages
	content_files : Iterable[Path] = [*CFG['content'].glob('**/*.md')]
	print(f"# Generating {len(content_files)} pages:\n\t{[str(x) for x in content_files]}")
	for md_path in content_files:
		gen_page(md_path)


def process_single():
	"""only for testing purposes"""
	fname : str = sys.argv[1].removesuffix('.md')
	cmd, _ = gen_cmd(fname)
	print(' '.join(cmd))

	out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
	
	print(out.stderr.decode('utf-8'))


if __name__ == "__main__":
	
	# load the config file
	config_file : str = sys.argv[1]
	CFG = yaml.full_load(open(config_file, 'r'))

	gen_all_pages()



