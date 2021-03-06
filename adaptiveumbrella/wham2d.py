import os
import subprocess
import pandas as pd
import numpy as np
import math

# ignore SettingsWithCopyWarning
pd.options.mode.chained_assignment = None

from adaptiveumbrella import UmbrellaRunner


class WHAM2DRunner(UmbrellaRunner):
    """ Umbrella runner implementation that uses wham-2d to perform
    the pmf calculation.

    Attributes:
        WHAM_EXEC: path to wham executeable

        """

    def __init__(self):
        UmbrellaRunner.__init__(self)
        self.verbose = False
        self.WHAM_EXEC = 'wham-2d'
        self.tmp_folder = "tmp/WHAM"
        self.simulation_folder = "tmp/simulations"

        if not os.path.exists(self.tmp_folder):
            os.makedirs(self.tmp_folder)

    def create_metadata_file(self):
        """ create the metadata file for wham-2d """
        path = os.path.join(self.tmp_folder, "{}_metadata.dat".format(self.num_iterations))
        with open(path, 'w') as out:
            for x, y in self._get_sampled_lambdas():
                colvar_file = os.path.join(self.simulation_folder, f"umb_{x}_{y}", "COLVAR")
                if not os.path.exists(colvar_file) and self.verbose:
                    print("Not found: {}".format(colvar_file))
                    continue
                out.write("{file}\t{x}\t{y}\t{fc_x}\t{fc_y}\n".format(
                    file=colvar_file, x=x, y=y, fc_x=self.whamconfig['fc_x'], fc_y=self.whamconfig['fc_y']
                ))
        return path


    def get_wham_output_file(self):
        """ Output file for wham-2d """
        return os.path.join(self.tmp_folder, f'{self.num_iterations}_freeenergy.dat')

    def get_wham_borders(self):
        """ we need to find dimensions for wham-2d calculations
        that are as small as possible but contain all sampled frames. """
        nonzero = np.nonzero(self.sample_list)
        borders = np.array([
            self._get_lambdas_for_index((nonzero[0].min(), nonzero[1].min())),
            self._get_lambdas_for_index((nonzero[0].max(), nonzero[1].max()))
            ])

        borders = borders.flatten()
        # increase borders by 2 lambda step from minimal dimensions
        borders[0] -= 2*self.cvs[0][2]
        borders[1] -= 2*self.cvs[1][2]
        borders[2] += 2*self.cvs[0][2]
        borders[3] += 2*self.cvs[1][2]
        return borders

    def run_wham2d(self, metafile_path, output_path):
        """ Runs wham-2d with the given parameters. See http://membrane.urmc.rochester.edu/sites/default/files/wham/doc.html """
        if os.path.exists(output_path):
            print(f"skipping wham, {output_path} already exists.")
            return

        borders = self.get_wham_borders()
        cmd = "{exec} Px={px} {min_x} {max_x} {frames_x} Py={py} {min_y} {max_y} {frames_y} {tol} {temperature} 0 {metafile} {outfile} {mask}".format(
            exec=self.WHAM_EXEC,
            px=self.whamconfig['Px'],
            min_x=borders[0],
            max_x=borders[2],
            frames_x=self.whamconfig['num_bins_x'],
            py=self.whamconfig['Py'],
            min_y=borders[1],
            max_y=borders[3],
            frames_y=self.whamconfig['num_bins_y'],
            tol=self.whamconfig['tolerance'],
            temperature=self.whamconfig['temperature'],
            metafile=metafile_path,
            outfile=output_path,
            mask = self.whamconfig['mask']
        )
        if self.verbose:
            print(cmd)
            err_code = subprocess.call(cmd, shell=True)
        else:
            FNULL = open(os.devnull, 'w')
            err_code = subprocess.call(cmd, shell=True, stdout=FNULL)
        if err_code != 0:
            print("wham exited with error code {}".format(err_code))
            exit(1)

    def load_wham_pmf(self, wham_file):
        """ Load the new pmf into a pandas dataframe """
        df = pd.read_csv(wham_file, delim_whitespace=True, names=['x', 'y', 'e', 'pro'], skiprows=1, index_col=None)
        # if e is inf, that means this region is unsampled. we cannot really 
        # tell its energy and set it to NaN 
        df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=['e'], how='all')
        return df

    def update_pmf(self, wham_pmf):
        """ Update the internal pmf representation from the pmf generated with wham """
        # map all points of self.pmf to one point of the wham_pmf and update self.pmf accordingly
        for x in range(self.pmf.shape[0]):
            for y in range(self.pmf.shape[1]):
                lambdax, lambday = self._get_lambdas_for_index((x, y))
                reduced = wham_pmf[ (abs(wham_pmf.x-lambdax) < self.cvs[0][2]) & (abs(wham_pmf.y-lambday) < self.cvs[1][2]) ]
                if len(reduced) == 0:
                    self.pmf[x,y] = np.inf
                else:
                    reduced['dist'] = reduced.apply(lambda row: np.linalg.norm((lambdax-row['x'], lambday-row['y'])), axis=1)
                    min = reduced[reduced.dist == reduced.dist.min()]
                    min_row = reduced[(reduced.x == min.x.iloc[0]) & (reduced.y == min.y.iloc[0])]
                    self.pmf[x, y] = min_row.e.iloc[0]

    def calculate_new_pmf(self):
        metafile_path = self.create_metadata_file()
        wham_pmf_file = self.get_wham_output_file()
        self.run_wham2d(metafile_path, wham_pmf_file)

        wham_pmf = self.load_wham_pmf(wham_pmf_file)
        self.update_pmf(wham_pmf)
        return self.pmf
